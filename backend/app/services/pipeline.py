"""PipelineOrchestrator — audio processing pipeline with checkpoints.

Checkpoints (files.stage):
  0 — uploaded (file on disk, record in DB)
  1 — transcribed (Whisper → saved to transcriptions table)
  2 — diarized (pyannote/channel-split → saved to diarizations table)
  3 — analyzed (GPT-4 → saved to analyses table)
  4 — done (all complete)

Checkpoint recovery: if stage >= N, skip stage N (resume from last checkpoint).
Graceful degradation:
  LLM fails  → status=done, analysis=None (shows transcript+diarization)
  Diarize fails → status=failed
  Whisper fails → status=failed
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import File, Transcription, Diarization, Analysis

logger = logging.getLogger(__name__)

# Progress milestones after each stage completes
STAGE_PROGRESS = {
    0: 0,
    1: 40,    # transcription done
    2: 70,    # diarization done
    3: 90,    # analysis done
    4: 100,   # all done
}

STAGE_STATUS = {
    1: "transcribing",
    2: "diarizing",
    3: "analyzing",
}


class PipelineOrchestrator:
    """Processes a single file through all pipeline stages."""

    def __init__(self, db: Session) -> None:
        self.db = db

    async def process_file(self, file_id: uuid.UUID) -> None:
        """Run the full pipeline for a file, resuming from last checkpoint."""
        db_file = self.db.get(File, file_id)
        if db_file is None:
            logger.error("Pipeline: file %s not found", file_id)
            return

        logger.info(
            "Pipeline: starting file %s (stage=%d, status=%s)",
            file_id, db_file.stage, db_file.status,
        )

        # --- Stage 1: Transcription ---
        transcription_result = None
        if db_file.stage < 1:
            self._set_status(db_file, "transcribing", stage=1, progress=5)
            try:
                transcription_result = await self._run_transcription(db_file)
                self._save_transcription(db_file, transcription_result)
                self._set_status(db_file, "transcribing", stage=1, progress=STAGE_PROGRESS[1])
            except Exception as exc:
                self._fail(db_file, f"Транскрибация: {exc}")
                logger.error("Stage 1 failed for %s: %s", file_id, exc, exc_info=True)
                return
        else:
            # Load from DB checkpoint
            transcription_result = self._load_transcription(db_file)
            logger.info("Stage 1 skipped (checkpoint): %s", file_id)

        # --- Stage 2: Diarization ---
        diarization_result = None
        if db_file.stage < 2:
            self._set_status(db_file, "diarizing", stage=2, progress=STAGE_PROGRESS[1] + 5)
            try:
                word_timestamps = transcription_result.word_timestamps if transcription_result else []
                diarization_result = await self._run_diarization(db_file, word_timestamps)
                self._save_diarization(db_file, diarization_result)
                self._set_status(db_file, "diarizing", stage=2, progress=STAGE_PROGRESS[2])
            except Exception as exc:
                self._fail(db_file, f"Диаризация: {exc}")
                logger.error("Stage 2 failed for %s: %s", file_id, exc, exc_info=True)
                return
        else:
            diarization_result = self._load_diarization(db_file)
            logger.info("Stage 2 skipped (checkpoint): %s", file_id)

        # --- Stage 3: LLM Analysis (non-fatal) ---
        if db_file.stage < 3:
            self._set_status(db_file, "analyzing", stage=3, progress=STAGE_PROGRESS[2] + 5)
            try:
                analysis_result = await self._run_analysis(db_file, diarization_result)
                if analysis_result is not None:
                    self._save_analysis(db_file, analysis_result)
                    logger.info(
                        "LLM analysis for %s: overall=%d", file_id, analysis_result.overall
                    )
                else:
                    logger.warning(
                        "LLM unavailable for %s — graceful degradation (no analysis)", file_id
                    )
            except Exception as exc:
                # Non-fatal: log but continue to done
                logger.error(
                    "Stage 3 (LLM) failed for %s: %s — continuing without analysis",
                    file_id, exc,
                )
        else:
            logger.info("Stage 3 skipped (checkpoint): %s", file_id)

        # --- Stage 4: Done ---
        self._set_status(db_file, "done", stage=4, progress=STAGE_PROGRESS[4])
        logger.info("Pipeline complete for %s", file_id)

    # ------------------------------------------------------------------
    # Stage runners
    # ------------------------------------------------------------------

    async def _run_transcription(self, db_file: File) -> Any:
        """Run Whisper transcription (sync, runs in thread)."""
        import asyncio
        from app.services.whisper_service import WhisperService

        if not db_file.audio_path:
            raise ValueError("audio_path is None — file not on disk?")

        whisper = WhisperService.get_instance()
        loop = asyncio.get_running_loop()
        # Run blocking call in thread pool to not block event loop
        result = await loop.run_in_executor(
            None, whisper.transcribe, db_file.audio_path
        )
        return result

    async def _run_diarization(self, db_file: File, word_timestamps: list[dict]) -> Any:
        import asyncio
        from app.services.diarization import DiarizationService

        if not db_file.audio_path:
            raise ValueError("audio_path is None")

        diarizer = DiarizationService.get_instance()
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, diarizer.diarize, db_file.audio_path, word_timestamps
        )
        return result

    async def _run_analysis(self, db_file: File, diarization_result: Any) -> Any:
        import asyncio
        from app.services.llm_service import LLMService

        llm = LLMService.get_instance()

        operator_text = ""
        client_text = ""
        if diarization_result is not None:
            operator_text = " ".join(
                seg.text for seg in diarization_result.transcript_segments
                if seg.speaker == "operator"
            )
            client_text = " ".join(
                seg.text for seg in diarization_result.transcript_segments
                if seg.speaker == "client"
            )
        else:
            # Load transcription text as fallback
            tr = self.db.scalar(
                sa_select(Transcription).where(
                    Transcription.file_id == db_file.id
                )
            )
            if tr:
                operator_text = tr.full_text

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, llm.analyze, operator_text, client_text
        )
        return result

    # ------------------------------------------------------------------
    # DB persistence helpers
    # ------------------------------------------------------------------

    def _save_transcription(self, db_file: File, result: Any) -> None:
        # Remove existing (idempotent on retry)
        existing = self.db.scalar(
            sa_select(Transcription).where(
                Transcription.file_id == db_file.id
            )
        )
        if existing:
            self.db.delete(existing)
            self.db.flush()

        tr = Transcription(
            file_id=db_file.id,
            full_text=result.full_text,
            word_timestamps=result.word_timestamps,
            language="ru",
        )
        self.db.add(tr)
        self.db.commit()
        logger.debug("Saved transcription for %s (%d words)", db_file.id, len(result.word_timestamps))

    def _save_diarization(self, db_file: File, result: Any) -> None:
        existing = self.db.scalar(
            sa_select(Diarization).where(
                Diarization.file_id == db_file.id
            )
        )
        if existing:
            self.db.delete(existing)
            self.db.flush()

        segments_json = [
            {
                "speaker": seg.speaker,
                "start": seg.start,
                "end": seg.end,
                "text": seg.text,
            }
            for seg in result.transcript_segments
        ]
        diar = Diarization(
            file_id=db_file.id,
            segments=segments_json,
            method=result.method,
            confidence=result.confidence,
            num_speakers=result.num_speakers,
        )
        self.db.add(diar)
        self.db.commit()
        logger.debug("Saved diarization for %s (%d segments)", db_file.id, len(segments_json))

    def _save_analysis(self, db_file: File, result: Any) -> None:
        existing = self.db.scalar(
            sa_select(Analysis).where(
                Analysis.file_id == db_file.id
            )
        )
        if existing:
            self.db.delete(existing)
            self.db.flush()

        analysis = Analysis(
            file_id=db_file.id,
            standard=result.standard,
            loyalty=result.loyalty,
            kindness=result.kindness,
            overall=result.overall,
            summary=result.summary,
            quotes=result.quotes,
            llm_model=result.llm_model,
        )
        self.db.add(analysis)
        self.db.commit()

    # ------------------------------------------------------------------
    # DB checkpoint loaders
    # ------------------------------------------------------------------

    def _load_transcription(self, db_file: File) -> Any | None:
        from sqlalchemy import select as sa_select
        from app.services.whisper_service import TranscriptionResult

        tr = self.db.scalar(sa_select(Transcription).where(Transcription.file_id == db_file.id))
        if tr is None:
            logger.warning("Stage 1 checkpoint missing for %s — re-running", db_file.id)
            return None
        return TranscriptionResult(
            full_text=tr.full_text,
            word_timestamps=tr.word_timestamps or [],
        )

    def _load_diarization(self, db_file: File) -> Any | None:
        from sqlalchemy import select as sa_select
        from app.services.diarization import DiarizationResult, DiarizationSegment, TranscriptSegment

        diar = self.db.scalar(sa_select(Diarization).where(Diarization.file_id == db_file.id))
        if diar is None:
            logger.warning("Stage 2 checkpoint missing for %s — will re-run diarization", db_file.id)
            return None

        transcript_segments = [
            TranscriptSegment(
                speaker=seg["speaker"],
                start=seg["start"],
                end=seg["end"],
                text=seg["text"],
            )
            for seg in (diar.segments or [])
        ]
        return DiarizationResult(
            segments=[DiarizationSegment(seg["speaker"], seg["start"], seg["end"])
                      for seg in (diar.segments or [])],
            transcript_segments=transcript_segments,
            method=diar.method or "unknown",
            confidence=diar.confidence,
            num_speakers=diar.num_speakers or 1,
        )

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def _set_status(
        self,
        db_file: File,
        status: str,
        stage: int,
        progress: int,
    ) -> None:
        db_file.status = status
        db_file.stage = stage
        db_file.progress = progress
        self.db.commit()
        # Fire-and-forget broadcast (non-blocking)
        self._broadcast(str(db_file.id), status, progress, stage)

    def _broadcast(self, file_id: str, status: str, progress: int, stage: int) -> None:
        """Broadcast progress to WebSocket subscribers (non-blocking)."""
        import asyncio
        try:
            from app.routers.ws import ws_manager
            loop = asyncio.get_running_loop()
            loop.create_task(
                ws_manager.broadcast_progress(file_id, status, progress, stage)
            )
        except RuntimeError:
            # No running loop — skip broadcast (e.g. during tests)
            pass
        except Exception as exc:
            logger.debug("WS broadcast skipped: %s", exc)

    def _fail(self, db_file: File, error: str) -> None:
        db_file.status = "failed"
        db_file.error_message = error
        db_file.retry_count = (db_file.retry_count or 0) + 1
        self.db.commit()
        self._broadcast(str(db_file.id), "failed", db_file.progress or 0, db_file.stage or 0)
        logger.error("File %s failed: %s", db_file.id, error)
