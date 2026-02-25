"""WhisperService — audio transcription using faster-whisper on GPU.

Features:
- faster-whisper large-v3 on CUDA (RTX 5090)
- Voice Activity Detection (silero-vad) before transcription
- Chunking for files > 30 min with 1-sec overlap
- Word-level timestamps (for UI sync)
- Retry logic: 3x with exponential backoff
"""

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import soundfile as sf
import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

# Chunking constants
CHUNK_DURATION_SEC = 30 * 60          # 30 minutes
CHUNK_OVERLAP_SEC = 1                  # 1 second overlap between chunks
SAMPLE_RATE = 16000                    # Whisper expects 16 kHz mono

# Retry
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0                 # seconds


class TranscriptionResult:
    """Result of a transcription."""

    def __init__(self, full_text: str, word_timestamps: list[dict[str, Any]]):
        self.full_text = full_text
        self.word_timestamps = word_timestamps  # [{word, start, end}, ...]


class WhisperService:
    """Transcription service using faster-whisper large-v3 on GPU."""

    _instance: "WhisperService | None" = None
    _model: Any = None

    def __init__(self) -> None:
        self._model = None

    # Singleton — model load is expensive
    @classmethod
    def get_instance(cls) -> "WhisperService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_model(self) -> None:
        """Lazy-load the faster-whisper model."""
        if self._model is not None:
            return

        from faster_whisper import WhisperModel

        device = settings.whisper_device  # "cuda" | "cpu"
        compute_type = "float16" if device == "cuda" else "int8"

        logger.info(
            "Loading Whisper model %s on %s (compute=%s) …",
            settings.whisper_model,
            device,
            compute_type,
        )
        self._model = WhisperModel(
            settings.whisper_model,
            device=device,
            compute_type=compute_type,
        )
        logger.info("Whisper model loaded")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def transcribe(self, audio_path: str) -> TranscriptionResult:
        """Transcribe an audio file.

        For files > 30 min: splits into chunks with 1-sec overlap, then
        merges results (adjusting timestamps per chunk offset).

        Returns:
            TranscriptionResult with full_text and word_timestamps.
        """
        self._load_model()
        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        duration = self._get_duration(path)
        logger.info("Transcribing %s (%.1f sec)", path.name, duration)

        if duration > CHUNK_DURATION_SEC:
            return self._transcribe_chunked(path, duration)
        else:
            vad_path = self._apply_vad(path)
            try:
                return self._transcribe_with_retry(vad_path, offset_sec=0.0)
            finally:
                if vad_path != path:
                    vad_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_duration(self, path: Path) -> float:
        """Get audio duration via soundfile (fast, no subprocess)."""
        try:
            info = sf.info(str(path))
            return info.duration
        except Exception:
            import subprocess, json
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_format", str(path)],
                capture_output=True, text=True, timeout=30,
            )
            data = json.loads(result.stdout)
            return float(data["format"]["duration"])

    def _apply_vad(self, path: Path) -> Path:
        """Apply Voice Activity Detection using silero-vad.

        Loads audio via soundfile (avoids torchaudio compatibility issues),
        runs silero-vad model, keeps only speech segments.

        Returns path to a temp WAV (16 kHz mono) with only speech.
        Falls back to original path on any error (safe degradation).
        """
        try:
            import torch
            from silero_vad import load_silero_vad, get_speech_timestamps

            # Load audio as 16 kHz mono via ffmpeg → numpy → torch tensor
            audio_np = self._load_as_16k_mono(path).copy()  # writable copy for torch
            audio_tensor = torch.from_numpy(audio_np)

            model = load_silero_vad()
            timestamps = get_speech_timestamps(
                audio_tensor,
                model,
                sampling_rate=SAMPLE_RATE,
                threshold=0.4,
                min_silence_duration_ms=500,
                min_speech_duration_ms=250,
            )

            if not timestamps:
                logger.warning("VAD: no speech detected in %s, using full audio", path.name)
                return path

            # Concatenate speech segments
            speech_chunks = [audio_np[ts["start"]: ts["end"]] for ts in timestamps]
            speech_audio = np.concatenate(speech_chunks)

            speech_ratio = len(speech_audio) / max(len(audio_np), 1)
            logger.info(
                "VAD: kept %.1f%% of audio (%d segments) for %s",
                speech_ratio * 100,
                len(timestamps),
                path.name,
            )

            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            sf.write(tmp.name, speech_audio, SAMPLE_RATE)
            return Path(tmp.name)

        except Exception as exc:
            logger.warning("VAD failed (%s), using original audio: %s", exc, path.name)
            return path

    def _transcribe_chunk(self, chunk_path: Path, offset_sec: float) -> dict[str, Any]:
        """Transcribe a single audio chunk.

        Returns dict with keys: text, word_timestamps (timestamps adjusted by offset).
        """
        segments, _info = self._model.transcribe(
            str(chunk_path),
            language=settings.whisper_language,
            word_timestamps=True,
            vad_filter=False,       # We apply our own VAD before
            beam_size=5,
        )

        words: list[dict[str, Any]] = []
        text_parts: list[str] = []

        for segment in segments:
            text_parts.append(segment.text.strip())
            if segment.words:
                for word in segment.words:
                    words.append({
                        "word": word.word.strip(),
                        "start": round(word.start + offset_sec, 3),
                        "end": round(word.end + offset_sec, 3),
                    })

        return {
            "text": " ".join(text_parts).strip(),
            "word_timestamps": words,
        }

    def _transcribe_with_retry(
        self, path: Path, offset_sec: float = 0.0
    ) -> "TranscriptionResult":
        """Transcribe with exponential backoff retry."""
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = self._transcribe_chunk(path, offset_sec=offset_sec)
                return TranscriptionResult(
                    full_text=result["text"],
                    word_timestamps=result["word_timestamps"],
                )
            except Exception as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "Whisper attempt %d/%d failed (%s). Retrying in %.1fs…",
                        attempt, MAX_RETRIES, exc, delay,
                    )
                    time.sleep(delay)
                else:
                    logger.error("Whisper failed after %d attempts: %s", MAX_RETRIES, exc)

        raise RuntimeError(f"Whisper transcription failed after {MAX_RETRIES} retries") from last_exc

    def _transcribe_chunked(self, path: Path, duration: float) -> TranscriptionResult:
        """Split long audio into 30-min chunks and transcribe each.

        Chunks overlap by CHUNK_OVERLAP_SEC to avoid cutting words at boundaries.
        Word timestamps are adjusted by chunk offset so they're absolute.
        """
        logger.info(
            "File is %.1f min > 30 min — using chunked transcription", duration / 60
        )

        # Load full audio as 16 kHz mono numpy array
        audio_np = self._load_as_16k_mono(path)
        total_samples = len(audio_np)
        chunk_samples = CHUNK_DURATION_SEC * SAMPLE_RATE
        overlap_samples = CHUNK_OVERLAP_SEC * SAMPLE_RATE

        all_words: list[dict[str, Any]] = []
        all_text_parts: list[str] = []
        chunk_idx = 0
        start_sample = 0

        while start_sample < total_samples:
            end_sample = min(start_sample + chunk_samples + overlap_samples, total_samples)
            chunk_audio = audio_np[start_sample:end_sample]
            offset_sec = start_sample / SAMPLE_RATE

            # Write chunk to temp WAV
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                sf.write(tmp.name, chunk_audio, SAMPLE_RATE)
                chunk_path = Path(tmp.name)

            try:
                logger.info(
                    "Chunk %d: %.1f–%.1f min",
                    chunk_idx,
                    offset_sec / 60,
                    end_sample / SAMPLE_RATE / 60,
                )
                vad_path = self._apply_vad(chunk_path)
                result = self._transcribe_with_retry(vad_path, offset_sec=offset_sec)
                if vad_path != chunk_path:
                    vad_path.unlink(missing_ok=True)

                all_text_parts.append(result.full_text)
                all_words.extend(result.word_timestamps)
            finally:
                chunk_path.unlink(missing_ok=True)

            chunk_idx += 1
            # Advance by chunk size (without overlap — overlap only appended to end)
            start_sample += chunk_samples

        full_text = " ".join(t for t in all_text_parts if t).strip()
        logger.info(
            "Chunked transcription done: %d chunks, %d words", chunk_idx, len(all_words)
        )
        return TranscriptionResult(full_text=full_text, word_timestamps=all_words)

    def _load_as_16k_mono(self, path: Path) -> np.ndarray:
        """Load audio file as 16 kHz mono float32 numpy array via ffmpeg."""
        import subprocess

        cmd = [
            "ffmpeg", "-i", str(path),
            "-ar", str(SAMPLE_RATE),
            "-ac", "1",
            "-f", "f32le",
            "-loglevel", "quiet",
            "pipe:1",
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed to decode {path.name}")
        return np.frombuffer(result.stdout, dtype=np.float32)
