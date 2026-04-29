"""QueueManager — async FIFO queue for audio processing.

Параллельная обработка через asyncio.Semaphore (path B, без Postgres queue).
Concurrency задаётся `settings.pipeline_concurrency` (env PIPELINE_CONCURRENCY).

Pipeline почти полностью I/O-bound (OpenAI API), потому asyncio даёт реальный
speedup без multiprocess. True parallelism через несколько процессов потребует
Postgres durable queue с `SELECT FOR UPDATE SKIP LOCKED` (фаза 5+).

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

# Statuses that mean "was being processed when server died" or waiting in queue
RESUMABLE_STATUSES = {"queued", "transcribing", "diarizing", "analyzing"}


class QueueManager:
    """Async parallel queue (semaphore-bounded)."""

    _instance: "QueueManager | None" = None

    def __init__(self) -> None:
        self._queue: asyncio.Queue[uuid.UUID] = asyncio.Queue()
        self._running = False
        self._current: uuid.UUID | None = None  # «первый из активных» (для health)
        self._in_progress: set[uuid.UUID] = set()

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
        """Thread-safe enqueue from sync context (e.g. upload router).

        Бросает исключение наверх если очередь не приняла файл — иначе upload
        вернёт пользователю 200, но файл навсегда останется в status=queued
        (silent loss). Caller (upload.py) ловит и помечает файл failed.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(self._queue.put_nowait, file_id)
        except RuntimeError:
            # No running loop (e.g. tests) — put directly
            self._queue.put_nowait(file_id)
        except Exception as exc:
            logger.error("Failed to enqueue %s: %s", file_id, exc)
            raise

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
        """Параллельная обработка файлов через Semaphore.

        Один процесс воркера, но N файлов одновременно «в полёте» через asyncio.
        Pipeline почти полностью I/O-bound (OpenAI API), поэтому asyncio даёт
        реальный спид-ап без multiprocess.

        Concurrency = settings.pipeline_concurrency (env PIPELINE_CONCURRENCY).
        """
        from app.config import settings as _settings

        self._running = True
        sem = asyncio.Semaphore(_settings.pipeline_concurrency)
        in_flight: set[asyncio.Task] = set()
        logger.info(
            "Queue worker started (concurrency=%d)",
            _settings.pipeline_concurrency,
        )

        while self._running:
            try:
                # Полёт лишних задач не запускаем сверх concurrency
                try:
                    file_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    # Вычищаем завершённые задачи периодически
                    in_flight = {t for t in in_flight if not t.done()}
                    continue

                # Acquire без блокировки потока — ждём слот
                await sem.acquire()
                task = asyncio.create_task(
                    self._process_one(file_id, sem),
                    name=f"pipeline-{file_id}",
                )
                in_flight.add(task)
                # Чистим завершённые (предотвращаем утечку)
                in_flight = {t for t in in_flight if not t.done()}

            except asyncio.CancelledError:
                logger.info("Queue worker cancelled — waiting for %d in-flight task(s)", len(in_flight))
                # Дожидаемся текущих файлов перед остановкой (не убиваем pipeline в середине)
                if in_flight:
                    await asyncio.gather(*in_flight, return_exceptions=True)
                break
            except Exception as exc:
                logger.error("Queue worker error: %s", exc, exc_info=True)

        self._running = False
        logger.info("Queue worker stopped")

    async def _process_one(self, file_id: uuid.UUID, sem: asyncio.Semaphore) -> None:
        """Обработать ОДИН файл. Освобождает Semaphore в finally."""
        from app.services.pipeline import PipelineOrchestrator
        from app.database import SessionLocal

        self._in_progress.add(file_id)
        # Для health-эндпоинта показываем «первый из активных»
        self._current = next(iter(self._in_progress), None)

        logger.info("Processing file %s (in_flight=%d)", file_id, len(self._in_progress))
        db = SessionLocal()
        try:
            orchestrator = PipelineOrchestrator(db)
            await orchestrator.process_file(file_id)
        except Exception as exc:
            logger.error("Unhandled error processing %s: %s", file_id, exc, exc_info=True)
        finally:
            db.close()
            self._queue.task_done()
            self._in_progress.discard(file_id)
            self._current = next(iter(self._in_progress), None)
            sem.release()

    def stop(self) -> None:
        self._running = False

    @property
    def queue_length(self) -> int:
        return self._queue.qsize()

    @property
    def current_file_id(self) -> uuid.UUID | None:
        return self._current
