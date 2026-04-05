"""Периодическая очистка старых аудиофайлов с диска.

Использует настройку audio_retention_days из config.
Удаляет только файлы со статусом done/failed.
Результаты анализа в БД сохраняются — удаляется только аудио с диска.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import File

logger = logging.getLogger(__name__)

CLEANUP_INTERVAL_HOURS = 6


async def cleanup_old_files() -> None:
    """Удалить аудиофайлы старше audio_retention_days."""
    if settings.audio_retention_days <= 0:
        return

    cutoff = datetime.utcnow() - timedelta(days=settings.audio_retention_days)
    db = SessionLocal()
    try:
        old_files = db.scalars(
            select(File).where(
                File.created_at < cutoff,
                File.status.in_(("done", "failed")),
                File.audio_path.isnot(None),
            )
        ).all()

        if not old_files:
            return

        deleted_count = 0
        for f in old_files:
            if f.audio_path:
                p = Path(f.audio_path)
                if p.exists():
                    try:
                        p.unlink()
                        deleted_count += 1
                    except OSError as exc:
                        logger.warning("Cleanup: не удалось удалить %s: %s", p, exc)
                        continue
            f.audio_path = None

        db.commit()
        logger.info(
            "Cleanup: удалено %d/%d старых аудиофайлов (cutoff=%s)",
            deleted_count, len(old_files), cutoff.isoformat(),
        )
    except Exception as exc:
        logger.error("Cleanup error: %s", exc, exc_info=True)
        db.rollback()
    finally:
        db.close()


async def run_cleanup_loop() -> None:
    """Бесконечный цикл периодической очистки."""
    logger.info(
        "Cleanup scheduler started (retention=%d days, interval=%d hours)",
        settings.audio_retention_days,
        CLEANUP_INTERVAL_HOURS,
    )
    while True:
        try:
            await cleanup_old_files()
        except Exception as exc:
            logger.error("Cleanup loop error: %s", exc, exc_info=True)
        await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)
