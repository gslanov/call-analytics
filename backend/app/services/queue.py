"""QueueManager — in-memory async FIFO queue for audio processing.

Sequential (1 file at a time) processing via asyncio.Queue.
On server startup — re-queues files stuck in non-terminal states
(transcribing, diarizing, analyzing) so they resume from their checkpoint.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Statuses that mean "was being processed when server died"
RESUMABLE_STATUSES = {"transcribing", "diarizing", "analyzing"}


class QueueManager:
    """Async in-memory FIFO queue."""

    _instance: "QueueManager | None" = None

    def __init__(self) -> None:
        self._queue: asyncio.Queue[uuid.UUID] = asyncio.Queue()
        self._running = False
        self._current: uuid.UUID | None = None

    @classmethod
    def get_instance(cls) -> "QueueManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def enqueue(self, file_id: uuid.UUID) -> None:
        """Add a file to the processing queue."""
        await self._queue.put(file_id)
        logger.info("Queued file %s (queue size: %d)", file_id, self._queue.qsize())

    def enqueue_sync(self, file_id: uuid.UUID) -> None:
        """Thread-safe enqueue from sync context (e.g. upload router)."""
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(self._queue.put_nowait, file_id)
        except RuntimeError:
            # No running loop (e.g. tests) — put directly
            self._queue.put_nowait(file_id)
        except Exception as exc:
            logger.error("Failed to enqueue %s: %s", file_id, exc)

    async def recover_interrupted(self, db: "Session") -> None:
        """On startup: re-queue files that were interrupted mid-processing."""
        from sqlalchemy import select
        from app.models import File

        stuck = db.scalars(
            select(File).where(File.status.in_(RESUMABLE_STATUSES))
        ).all()

        if not stuck:
            return

        logger.info(
            "Recovering %d interrupted file(s): %s",
            len(stuck),
            [str(f.id) for f in stuck],
        )
        for f in stuck:
            # Reset to queued so pipeline re-picks from checkpoint
            f.status = "queued"
            db.commit()
            await self._queue.put(f.id)

    async def process_queue(self) -> None:
        """Infinite loop — process files one at a time."""
        from app.services.pipeline import PipelineOrchestrator
        from app.database import SessionLocal

        self._running = True
        logger.info("Queue worker started")

        while self._running:
            try:
                # Wait for next file (timeout=1s so we can check _running flag)
                try:
                    file_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                self._current = file_id
                logger.info("Processing file %s", file_id)

                db = SessionLocal()
                try:
                    orchestrator = PipelineOrchestrator(db)
                    await orchestrator.process_file(file_id)
                except Exception as exc:
                    logger.error("Unhandled error processing %s: %s", file_id, exc, exc_info=True)
                finally:
                    db.close()
                    self._queue.task_done()
                    self._current = None

            except asyncio.CancelledError:
                logger.info("Queue worker cancelled")
                break
            except Exception as exc:
                logger.error("Queue worker error: %s", exc, exc_info=True)

        self._running = False
        logger.info("Queue worker stopped")

    def stop(self) -> None:
        self._running = False

    @property
    def queue_length(self) -> int:
        return self._queue.qsize()

    @property
    def current_file_id(self) -> uuid.UUID | None:
        return self._current
