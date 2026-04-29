"""Периодическая очистка старых аудиофайлов с диска + сирот .tmp в uploads_dir.

Использует настройку audio_retention_days из config.
Удаляет только файлы со статусом done/failed.
Результаты анализа в БД сохраняются — удаляется только аудио с диска.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import File

logger = logging.getLogger(__name__)

CLEANUP_INTERVAL_HOURS = 6
TMP_ORPHAN_AGE_SEC = 6 * 3600  # .tmp старше 6 часов считаем сиротой (upload не дошёл до rename)


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
                        # audio_path обнуляем ТОЛЬКО после успешного unlink
                        f.audio_path = None
                    except OSError as exc:
                        logger.warning("Cleanup: не удалось удалить %s: %s", p, exc)
                        continue
                else:
                    # Файл уже физически отсутствует — синхронизируем БД
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


def cleanup_orphan_tmp() -> int:
    """Удалить .tmp файлы в uploads_dir старше TMP_ORPHAN_AGE_SEC.

    .tmp создаётся при streaming upload. Если процесс упал между write и rename,
    .tmp останется навсегда. Запускается на старте + периодически.
    """
    uploads = Path(settings.uploads_dir)
    if not uploads.exists():
        return 0

    now = time.time()
    deleted = 0
    for p in uploads.glob("*.tmp"):
        try:
            if not p.is_file():
                continue
            age = now - p.stat().st_mtime
            if age < TMP_ORPHAN_AGE_SEC:
                continue
            p.unlink()
            deleted += 1
            logger.info("Orphan tmp удалён: %s (age=%.0fs)", p.name, age)
        except OSError as exc:
            logger.warning("Не смог удалить orphan .tmp %s: %s", p, exc)
    # Также скрытые .{uuid}.ext.tmp
    for p in uploads.glob(".*.tmp"):
        try:
            if not p.is_file():
                continue
            age = now - p.stat().st_mtime
            if age < TMP_ORPHAN_AGE_SEC:
                continue
            p.unlink()
            deleted += 1
            logger.info("Orphan hidden tmp удалён: %s (age=%.0fs)", p.name, age)
        except OSError as exc:
            logger.warning("Не смог удалить orphan .tmp %s: %s", p, exc)
    return deleted


async def run_cleanup_loop() -> None:
    """Бесконечный цикл периодической очистки."""
    logger.info(
        "Cleanup scheduler started (retention=%d days, interval=%d hours)",
        settings.audio_retention_days,
        CLEANUP_INTERVAL_HOURS,
    )
    # Сразу подчищаем сирот при старте
    try:
        cleanup_orphan_tmp()
    except Exception as exc:
        logger.error("Initial orphan cleanup error: %s", exc, exc_info=True)

    while True:
        try:
            await cleanup_old_files()
        except Exception as exc:
            logger.error("Cleanup loop error: %s", exc, exc_info=True)
        try:
            cleanup_orphan_tmp()
        except Exception as exc:
            logger.error("Orphan tmp cleanup error: %s", exc, exc_info=True)
        await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)
