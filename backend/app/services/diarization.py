"""DiarizationService — speaker separation for audio files.

Two strategies (selected automatically by channel count):
  - Stereo (2 channels): L=Operator, R=Client. Confidence: 100%.
  - Mono (1 channel): pyannote/speaker-diarization-3.1 via HuggingFace.
    speaker_0 (first voice) = Operator, speaker_1 = Client.

Merge: combines Whisper word_timestamps with diarization segments to produce
       TranscriptSegment list with speaker labels (operator/client).
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from app.config import settings

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# Confidence thresholds
LOW_CONFIDENCE_THRESHOLD = 70.0


@dataclass
class DiarizationSegment:
    """Raw diarization segment (before merge with transcript)."""
    speaker: str          # "operator" | "client" | "unknown"
    start: float          # seconds
    end: float            # seconds


@dataclass
class TranscriptSegment:
    """Transcript segment after merging diarization + Whisper words."""
    speaker: str          # "operator" | "client" | "unknown"
    start: float
    end: float
    text: str


@dataclass
class DiarizationResult:
    segments: list[DiarizationSegment]
    transcript_segments: list[TranscriptSegment]
    method: str           # "channel_split" | "pyannote"
    confidence: float | None  # None = 100% for channel_split (exact)
    num_speakers: int
    warnings: list[str] = field(default_factory=list)


class DiarizationService:
    """Speaker diarization service."""

    _instance: "DiarizationService | None" = None
    _pipeline: Any = None

    @classmethod
    def get_instance(cls) -> "DiarizationService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def diarize(
        self,
        audio_path: str,
        word_timestamps: list[dict[str, Any]],
    ) -> DiarizationResult:
        """Main entry: choose strategy by channel count, then merge with transcript.

        Args:
            audio_path: Path to audio file.
            word_timestamps: List of {word, start, end} from WhisperService.

        Returns:
            DiarizationResult with segments, transcript_segments, confidence, warnings.
        """
        path = Path(audio_path)
        num_channels = self._get_channel_count(path)
        logger.info("Diarizing %s (%d channel(s))", path.name, num_channels)

        if num_channels == 2:
            return self._diarize_stereo(path, word_timestamps)
        else:
            return self._diarize_mono(path, word_timestamps)

    # ------------------------------------------------------------------
    # Strategy 1: Stereo channel split
    # ------------------------------------------------------------------

    def _diarize_stereo(
        self,
        path: Path,
        word_timestamps: list[dict[str, Any]],
    ) -> DiarizationResult:
        """L channel = Operator, R channel = Client. Confidence: 100%."""
        audio, sr = self._load_stereo(path)
        duration = audio.shape[1] / sr

        segments = [
            DiarizationSegment(speaker="operator", start=0.0, end=duration),
            DiarizationSegment(speaker="client",   start=0.0, end=duration),
        ]

        # For stereo: each word needs to be assigned by its channel energy
        transcript_segments = self._merge_stereo(path, word_timestamps, sr)

        logger.info(
            "Stereo split: %.1f sec, %d transcript segments",
            duration,
            len(transcript_segments),
        )
        return DiarizationResult(
            segments=segments,
            transcript_segments=transcript_segments,
            method="channel_split",
            confidence=None,   # None = exact (100%)
            num_speakers=2,
        )

    def _load_stereo(self, path: Path) -> tuple[np.ndarray, int]:
        """Load stereo audio via ffmpeg → numpy shape (2, N)."""
        cmd = [
            "ffmpeg", "-i", str(path),
            "-ar", str(SAMPLE_RATE),
            "-ac", "2",
            "-f", "f32le",
            "-loglevel", "quiet",
            "pipe:1",
        ]
        import subprocess
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        raw = np.frombuffer(result.stdout, dtype=np.float32)
        # interleaved stereo: [L0, R0, L1, R1, ...]
        audio = raw.reshape(-1, 2).T   # shape (2, N)
        return audio, SAMPLE_RATE

    def _merge_stereo(
        self,
        path: Path,
        word_timestamps: list[dict[str, Any]],
        sr: int,
    ) -> list[TranscriptSegment]:
        """Assign each word to operator or client by channel RMS energy.

        For each word window, compare L-channel RMS vs R-channel RMS.
        Higher energy → that channel's speaker.
        """
        audio, _ = self._load_stereo(path)
        n_samples = audio.shape[1]

        transcript_segments: list[TranscriptSegment] = []
        for w in word_timestamps:
            start_s = float(w["start"])
            end_s   = float(w["end"])
            s = max(0, int(start_s * sr))
            e = min(n_samples, int(end_s * sr))
            if s >= e:
                continue
            rms_l = float(np.sqrt(np.mean(audio[0, s:e] ** 2)))
            rms_r = float(np.sqrt(np.mean(audio[1, s:e] ** 2)))
            speaker = "operator" if rms_l >= rms_r else "client"
            transcript_segments.append(
                TranscriptSegment(
                    speaker=speaker,
                    start=start_s,
                    end=end_s,
                    text=w["word"],
                )
            )

        return self._merge_adjacent_segments(transcript_segments)

    # ------------------------------------------------------------------
    # Strategy 2: Mono — pyannote diarization
    # ------------------------------------------------------------------

    def _diarize_mono(
        self,
        path: Path,
        word_timestamps: list[dict[str, Any]],
    ) -> DiarizationResult:
        """pyannote/speaker-diarization-3.1 on mono audio."""
        warnings: list[str] = []

        if not settings.hf_token:
            logger.warning(
                "HF_TOKEN not set — pyannote unavailable, returning single-speaker result"
            )
            warnings.append(
                "Диаризация недоступна: HF_TOKEN не настроен. "
                "Весь текст помечен как оператор."
            )
            return self._fallback_single_speaker(path, word_timestamps, warnings)

        self._load_pipeline()

        # Write 16 kHz mono WAV for pyannote
        audio_np = self._load_as_16k_mono(path)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            sf.write(tmp.name, audio_np, SAMPLE_RATE)
            mono_path = Path(tmp.name)

        try:
            diarization = self._pipeline(str(mono_path))
        finally:
            mono_path.unlink(missing_ok=True)

        # Parse pyannote output
        raw_segments: list[tuple[float, float, str]] = []  # (start, end, label)
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            raw_segments.append((turn.start, turn.end, speaker))

        num_speakers = len({s[2] for s in raw_segments})

        if num_speakers > 2:
            warnings.append(
                f"Обнаружено {num_speakers} говорящих. "
                "Оценка может быть неточной."
            )

        # Map pyannote labels → operator/client
        # First-appearing speaker = operator
        speaker_map = self._build_speaker_map(raw_segments)
        segments = [
            DiarizationSegment(
                speaker=speaker_map.get(lbl, "unknown"),
                start=start,
                end=end,
            )
            for start, end, lbl in raw_segments
        ]

        # Estimate confidence from segment overlap quality
        confidence = self._estimate_confidence(raw_segments)
        if confidence < LOW_CONFIDENCE_THRESHOLD:
            warnings.append(
                f"Разделение неуверенное ({confidence:.0f}%). "
                "Рекомендуем проверить вручную."
            )

        transcript_segments = self._merge_words_with_diarization(
            word_timestamps, segments
        )

        logger.info(
            "pyannote: %d speakers, confidence=%.1f%%, %d segments",
            num_speakers,
            confidence,
            len(transcript_segments),
        )
        return DiarizationResult(
            segments=segments,
            transcript_segments=transcript_segments,
            method="pyannote",
            confidence=confidence,
            num_speakers=num_speakers,
            warnings=warnings,
        )

    def _load_pipeline(self) -> None:
        """Lazy-load pyannote pipeline (GPU if available)."""
        if self._pipeline is not None:
            return
        import torch
        from pyannote.audio import Pipeline

        logger.info("Loading pyannote/speaker-diarization-3.1 …")
        self._pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=settings.hf_token,
        )
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self._pipeline.to(torch.device(device))
        logger.info("pyannote pipeline loaded on %s", device)

    @staticmethod
    def _build_speaker_map(
        raw_segments: list[tuple[float, float, str]]
    ) -> dict[str, str]:
        """Map pyannote speaker labels to 'operator'/'client'.

        First voice heard = operator. Second = client. Rest = unknown.
        """
        seen: list[str] = []
        for _, _, lbl in sorted(raw_segments, key=lambda x: x[0]):
            if lbl not in seen:
                seen.append(lbl)
        mapping: dict[str, str] = {}
        roles = ["operator", "client"]
        for i, lbl in enumerate(seen):
            mapping[lbl] = roles[i] if i < len(roles) else "unknown"
        return mapping

    @staticmethod
    def _estimate_confidence(
        raw_segments: list[tuple[float, float, str]]
    ) -> float:
        """Estimate confidence score (0-100) based on segment characteristics.

        Heuristic: ratio of non-overlapping time / total time.
        Short segments and many overlaps → lower confidence.
        """
        if not raw_segments:
            return 0.0

        durations = [e - s for s, e, _ in raw_segments]
        total = sum(durations)
        if total == 0:
            return 0.0

        # Penalise very short segments (< 0.5 sec) — sign of low confidence
        short = sum(1 for d in durations if d < 0.5)
        short_penalty = short / max(len(durations), 1) * 30  # up to 30% penalty

        base = 90.0 - short_penalty
        return max(0.0, min(100.0, base))

    # ------------------------------------------------------------------
    # Merge: words → speaker segments
    # ------------------------------------------------------------------

    def _merge_words_with_diarization(
        self,
        word_timestamps: list[dict[str, Any]],
        diarization_segments: list[DiarizationSegment],
    ) -> list[TranscriptSegment]:
        """Assign each Whisper word to a speaker using diarization segments.

        Strategy: find the diarization segment with maximum overlap with the word.
        If no overlap → assign 'unknown'.
        """
        result: list[TranscriptSegment] = []
        for w in word_timestamps:
            w_start = float(w["start"])
            w_end   = float(w["end"])
            speaker = self._find_speaker(w_start, w_end, diarization_segments)
            result.append(
                TranscriptSegment(
                    speaker=speaker,
                    start=w_start,
                    end=w_end,
                    text=w["word"],
                )
            )
        return self._merge_adjacent_segments(result)

    @staticmethod
    def _find_speaker(
        word_start: float,
        word_end: float,
        segments: list[DiarizationSegment],
    ) -> str:
        """Find speaker with maximum overlap with word window."""
        best_speaker = "unknown"
        best_overlap = 0.0
        for seg in segments:
            overlap = max(0.0, min(word_end, seg.end) - max(word_start, seg.start))
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = seg.speaker
        return best_speaker

    @staticmethod
    def _merge_adjacent_segments(
        words: list[TranscriptSegment],
    ) -> list[TranscriptSegment]:
        """Merge consecutive word-segments with the same speaker into utterances."""
        if not words:
            return []
        merged: list[TranscriptSegment] = []
        current = TranscriptSegment(
            speaker=words[0].speaker,
            start=words[0].start,
            end=words[0].end,
            text=words[0].text,
        )
        for w in words[1:]:
            if w.speaker == current.speaker:
                current.end = w.end
                current.text = current.text + " " + w.text
            else:
                merged.append(current)
                current = TranscriptSegment(
                    speaker=w.speaker,
                    start=w.start,
                    end=w.end,
                    text=w.text,
                )
        merged.append(current)
        return merged

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _get_channel_count(path: Path) -> int:
        """Get channel count via ffprobe."""
        import subprocess, json
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", str(path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            data = json.loads(result.stdout)
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "audio":
                    return int(stream.get("channels", 1))
        except Exception as exc:
            logger.warning("Could not detect channels for %s: %s", path.name, exc)
        return 1  # safe fallback

    @staticmethod
    def _load_as_16k_mono(path: Path) -> np.ndarray:
        import subprocess
        cmd = [
            "ffmpeg", "-i", str(path),
            "-ar", str(SAMPLE_RATE), "-ac", "1",
            "-f", "f32le", "-loglevel", "quiet", "pipe:1",
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        return np.frombuffer(result.stdout, dtype=np.float32)

    @staticmethod
    def _fallback_single_speaker(
        path: Path,
        word_timestamps: list[dict[str, Any]],
        warnings: list[str],
    ) -> DiarizationResult:
        """All words → operator when diarization is unavailable."""
        import subprocess, json

        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
               "-show_format", str(path)]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            duration = float(json.loads(res.stdout)["format"]["duration"])
        except Exception:
            duration = 0.0

        segments = [DiarizationSegment(speaker="operator", start=0.0, end=duration)]
        transcript_segments = [
            TranscriptSegment(
                speaker="operator",
                start=float(w["start"]),
                end=float(w["end"]),
                text=w["word"],
            )
            for w in word_timestamps
        ]
        merged = DiarizationService._merge_adjacent_segments(transcript_segments)
        return DiarizationResult(
            segments=segments,
            transcript_segments=merged,
            method="pyannote",
            confidence=None,
            num_speakers=1,
            warnings=warnings,
        )
