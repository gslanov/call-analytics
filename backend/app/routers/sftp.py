"""SFTP Files API — browse, stream, download and process files from MANGO SFTP directory."""

import uuid
from datetime import datetime, date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import File as FileModel, Operator
from app.services.audio_validator import validate_audio_file, _probe_audio
from app.services.queue import QueueManager

router = APIRouter(tags=["sftp"])

_MIME_MAP = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".webm": "audio/webm",
}


class SftpFileItem(BaseModel):
    filename: str
    size: int
    created_at: datetime
    duration_sec: float | None


class SftpFilesResponse(BaseModel):
    items: list[SftpFileItem]
    total: int
    page: int
    limit: int


class ProcessRequest(BaseModel):
    filenames: list[str]
    operator_name: str


class ProcessResponse(BaseModel):
    file_ids: list[str]
    operator: str
    status: str
    total_files: int


def _resolve_sftp_path(filename: str) -> Path:
    """Resolve filename to absolute path within SFTP dir (prevent path traversal)."""
    sftp_dir = Path(settings.mango_sftp_dir)
    safe_name = Path(filename).name
    full_path = sftp_dir / safe_name
    try:
        full_path.resolve().relative_to(sftp_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename")
    return full_path


@router.get("/sftp/files", response_model=SftpFilesResponse)
def list_sftp_files(
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    duration_min: float | None = None,
    duration_max: float | None = None,
    page: int = 1,
    limit: int = 50,
) -> SftpFilesResponse:
    """Список файлов из SFTP-директории MANGO с метаданными."""
    sftp_dir = Path(settings.mango_sftp_dir)
    if not sftp_dir.exists():
        return SftpFilesResponse(items=[], total=0, page=page, limit=limit)

    files: list[SftpFileItem] = []
    for file_path in sorted(sftp_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in _MIME_MAP:
            continue
        stat = file_path.stat()
        created_at = datetime.fromtimestamp(stat.st_mtime)
        duration_sec, _, _ = _probe_audio(file_path)
        files.append(SftpFileItem(
            filename=file_path.name,
            size=stat.st_size,
            created_at=created_at,
            duration_sec=duration_sec,
        ))

    if q:
        q_lower = q.lower()
        files = [f for f in files if q_lower in f.filename.lower()]
    if date_from:
        files = [f for f in files if f.created_at.date() >= date_from]
    if date_to:
        files = [f for f in files if f.created_at.date() <= date_to]
    if duration_min is not None:
        files = [f for f in files if f.duration_sec is not None and f.duration_sec >= duration_min]
    if duration_max is not None:
        files = [f for f in files if f.duration_sec is not None and f.duration_sec <= duration_max]

    total = len(files)
    offset = (page - 1) * limit
    items = files[offset: offset + limit]

    return SftpFilesResponse(items=items, total=total, page=page, limit=limit)


@router.get("/sftp/files/{filename}/stream")
def stream_sftp_file(filename: str) -> FileResponse:
    """Стриминг аудиофайла из SFTP-директории для воспроизведения в плеере."""
    file_path = _resolve_sftp_path(filename)
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    ext = file_path.suffix.lower()
    media_type = _MIME_MAP.get(ext, "audio/mpeg")
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=filename,
        headers={"Accept-Ranges": "bytes"},
    )


@router.get("/sftp/files/{filename}/download")
def download_sftp_file(filename: str) -> FileResponse:
    """Скачать аудиофайл из SFTP-директории."""
    file_path = _resolve_sftp_path(filename)
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    ext = file_path.suffix.lower()
    media_type = _MIME_MAP.get(ext, "application/octet-stream")
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/sftp/process", response_model=ProcessResponse)
def process_sftp_files(
    body: ProcessRequest,
    db: Session = Depends(get_db),
) -> ProcessResponse:
    """Отправить файлы из SFTP-директории на обработку Whisper."""
    if not body.operator_name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Поле operator_name не может быть пустым",
        )
    if not body.filenames:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Список файлов пуст")

    # Get or create operator
    op = db.scalar(select(Operator).where(Operator.name == body.operator_name.strip()))
    if op is None:
        op = Operator(name=body.operator_name.strip())
        db.add(op)
        db.flush()

    # Pre-load existing hashes for dedup
    existing_rows = db.execute(
        select(FileModel.file_hash, FileModel.id).where(FileModel.status != "failed")
    ).all()
    hash_to_file_id: dict[str, uuid.UUID] = {row.file_hash: row.id for row in existing_rows}

    accepted_file_ids: list[str] = []
    dest_dir = Path(settings.uploads_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    for filename in body.filenames:
        file_path = _resolve_sftp_path(filename)
        if not file_path.exists():
            continue

        content = file_path.read_bytes()
        result = validate_audio_file(filename, content, existing_hashes=set(hash_to_file_id.keys()))

        if not result.valid:
            if result.error and result.error.startswith("duplicate:"):
                file_hash = result.error.split(":", 1)[1]
                existing_id = hash_to_file_id.get(file_hash)
                if existing_id:
                    accepted_file_ids.append(str(existing_id))
            continue

        ext = Path(filename).suffix.lower()
        file_id = uuid.uuid4()
        dest = dest_dir / f"{file_id}{ext}"
        dest.write_bytes(content)

        db_file = FileModel(
            id=file_id,
            operator_id=op.id,
            original_name=filename,
            file_hash=result.file_hash,
            file_size=len(content),
            duration_sec=result.duration_sec,
            audio_path=str(dest),
            status="queued",
            stage=0,
        )
        db.add(db_file)
        hash_to_file_id[result.file_hash] = file_id
        accepted_file_ids.append(str(file_id))

    db.commit()

    q = QueueManager.get_instance()
    for fid_str in accepted_file_ids:
        fid = uuid.UUID(fid_str)
        row = db.get(FileModel, fid)
        if row and row.status == "queued":
            q.enqueue_sync(fid)

    return ProcessResponse(
        file_ids=accepted_file_ids,
        operator=body.operator_name.strip(),
        status="queued",
        total_files=len(accepted_file_ids),
    )
