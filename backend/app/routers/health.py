"""GET /api/v1/health — comprehensive health check endpoint."""

import shutil

from fastapi import APIRouter
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal
from app.schemas import HealthResponse, ServiceHealth
from app.services.queue import QueueManager

router = APIRouter(tags=["health"])


def _check_database() -> ServiceHealth:
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return ServiceHealth(ok=True)
    except Exception as exc:
        return ServiceHealth(ok=False, detail=str(exc))
    finally:
        db.close()


def _check_whisper() -> ServiceHealth:
    try:
        from app.services.whisper_service import WhisperService
        svc = WhisperService.get_instance()
        if svc._model is not None:
            return ServiceHealth(ok=True, detail=f"model={settings.whisper_model} loaded")
        return ServiceHealth(ok=True, detail=f"model={settings.whisper_model} not yet loaded (lazy)")
    except Exception as exc:
        return ServiceHealth(ok=False, detail=str(exc))


def _check_llm() -> ServiceHealth:
    if not settings.openai_api_key:
        return ServiceHealth(ok=False, detail="OPENAI_API_KEY not set — LLM disabled")
    return ServiceHealth(ok=True, detail="API key configured")


def _check_disk() -> ServiceHealth:
    try:
        usage = shutil.disk_usage(settings.uploads_dir)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        used_pct = (usage.used / usage.total) * 100
        ok = free_gb > 1.0  # warn if less than 1 GB free
        return ServiceHealth(
            ok=ok,
            detail=f"{free_gb:.1f} GB free / {total_gb:.1f} GB total ({used_pct:.0f}% used)",
        )
    except Exception as exc:
        return ServiceHealth(ok=False, detail=str(exc))


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    """Подробный health check: БД, Whisper, LLM, диск, очередь."""
    db_health = _check_database()
    whisper_health = _check_whisper()
    llm_health = _check_llm()
    disk_health = _check_disk()

    q = QueueManager.get_instance()

    all_ok = all([db_health.ok, whisper_health.ok, disk_health.ok])
    status = "ok" if all_ok else ("degraded" if db_health.ok else "error")

    return HealthResponse(
        status=status,
        database=db_health,
        whisper=whisper_health,
        llm=llm_health,
        disk=disk_health,
        queue_length=q.queue_length,
        current_file=str(q.current_file_id) if q.current_file_id else None,
    )
