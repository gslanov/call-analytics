"""GET /api/v1/audio/{file_id} — stream audio file for playback."""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import File

router = APIRouter(tags=["audio"])

# MIME type map for supported audio formats
_MIME_MAP = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".webm": "audio/webm",
}


@router.get("/audio/{file_id}")
def stream_audio(
    file_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> FileResponse:
    """Отдать аудиофайл для воспроизведения. Поддерживает Range-запросы."""
    db_file = db.get(File, file_id)
    if db_file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    if not db_file.audio_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audio file not available on disk",
        )

    audio_path = Path(db_file.audio_path)
    if not audio_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audio file missing from disk",
        )

    ext = audio_path.suffix.lower()
    media_type = _MIME_MAP.get(ext, "audio/mpeg")

    return FileResponse(
        path=str(audio_path),
        media_type=media_type,
        filename=db_file.original_name,
        headers={"Accept-Ranges": "bytes"},
    )
