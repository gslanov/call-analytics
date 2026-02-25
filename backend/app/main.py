import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_worker_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _worker_task

    # Startup: ensure data dirs exist
    Path(settings.uploads_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.audio_dir).mkdir(parents=True, exist_ok=True)

    # Recover interrupted files and start queue worker
    from app.services.queue import QueueManager
    from app.database import SessionLocal

    q = QueueManager.get_instance()
    db = SessionLocal()
    try:
        await q.recover_interrupted(db)
    finally:
        db.close()

    _worker_task = asyncio.create_task(q.process_queue(), name="queue-worker")
    logger.info("Call Analytics API started (queue worker running)")

    yield

    # Graceful shutdown
    logger.info("Call Analytics API shutting down…")
    if _worker_task and not _worker_task.done():
        q.stop()
        _worker_task.cancel()
        try:
            await asyncio.wait_for(_worker_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    logger.info("Shutdown complete")


app = FastAPI(
    title="Call Analytics API",
    description="API для анализа качества звонков операторов контакт-центра",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "detail": "Внутренняя ошибка сервера"},
    )


from app.routers import audio, health, operators, results, upload, ws

app.include_router(upload.router, prefix="/api/v1")
app.include_router(ws.router, prefix="/api/v1")
app.include_router(results.router, prefix="/api/v1")
app.include_router(operators.router, prefix="/api/v1")
app.include_router(audio.router, prefix="/api/v1")
app.include_router(health.router, prefix="/api/v1")
