"""POST /api/v1/upload — batch audio file upload with validation and deduplication."""

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import File as FileModel, Operator
from app.schemas import UploadResponse, ValidationError
from app.services.audio_validator import validate_audio_file
from app.services.queue import QueueManager

router = APIRouter(tags=["upload"])

MAX_READ_SIZE = settings.max_file_size_mb * 1024 * 1024 + 1  # +1 to detect over-limit


def _get_or_create_operator(db: Session, name: str) -> Operator:
    op = db.scalar(select(Operator).where(Operator.name == name))
    if op is None:
        op = Operator(name=name)
        db.add(op)
        db.flush()
    return op


def _save_file_to_disk(file_id: uuid.UUID, ext: str, content: bytes) -> Path:
    dest_dir = Path(settings.uploads_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{file_id}{ext}"
    dest.write_bytes(content)
    return dest


@router.post("/upload", response_model=UploadResponse)
async def upload_files(
    files: list[UploadFile] = File(..., description="Аудиофайлы для анализа"),
    operator_name: str = Form(..., description="Имя оператора"),
    db: Session = Depends(get_db),
) -> UploadResponse:
    """Загрузить аудиофайлы для анализа качества звонка.

    - Валидирует формат, размер, целостность (ffprobe) и длительность
    - Дедуплицирует по SHA-256 (возвращает существующий file_id)
    - Сохраняет файлы в data/uploads/
    - Создаёт записи в БД со статусом 'queued'
    """
    if not operator_name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Поле operator_name не может быть пустым",
        )
    if len(files) > settings.max_batch_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Слишком много файлов. Максимум {settings.max_batch_size} за раз",
        )

    # Pre-load existing hashes for dedup (single query)
    existing_rows = db.execute(
        select(FileModel.file_hash, FileModel.id).where(
            FileModel.status != "failed"
        )
    ).all()
    hash_to_file_id: dict[str, uuid.UUID] = {row.file_hash: row.id for row in existing_rows}

    validation_errors: list[ValidationError] = []
    accepted_file_ids: list[str] = []

    operator = _get_or_create_operator(db, operator_name.strip())

    for upload in files:
        filename = upload.filename or "unknown"

        # Read with size guard
        content = await upload.read(MAX_READ_SIZE)
        if len(content) >= MAX_READ_SIZE:
            validation_errors.append(
                ValidationError(
                    file=filename,
                    error=f"Размер файла превышает лимит {settings.max_file_size_mb} MB",
                )
            )
            continue

        result = validate_audio_file(
            filename,
            content,
            existing_hashes=set(hash_to_file_id.keys()),
        )

        if not result.valid:
            # Deduplication: return existing file_id instead of error
            if result.error and result.error.startswith("duplicate:"):
                file_hash = result.error.split(":", 1)[1]
                existing_id = hash_to_file_id.get(file_hash)
                if existing_id:
                    accepted_file_ids.append(str(existing_id))
                    continue

            validation_errors.append(ValidationError(file=filename, error=result.error or "Неизвестная ошибка"))
            continue

        # Save to disk
        ext = Path(filename).suffix.lower()
        file_id = uuid.uuid4()
        audio_path = _save_file_to_disk(file_id, ext, content)

        # Create DB record
        db_file = FileModel(
            id=file_id,
            operator_id=operator.id,
            original_name=filename,
            file_hash=result.file_hash,
            file_size=len(content),
            duration_sec=result.duration_sec,
            audio_path=str(audio_path),
            status="queued",
            stage=0,
        )
        db.add(db_file)
        hash_to_file_id[result.file_hash] = file_id
        accepted_file_ids.append(str(file_id))

    # If ALL files failed validation → 400
    if validation_errors and not accepted_file_ids:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "validation_error", "details": [e.model_dump() for e in validation_errors]},
        )

    db.commit()

    # Enqueue new (non-duplicate) files for processing
    q = QueueManager.get_instance()
    for fid_str in accepted_file_ids:
        fid = uuid.UUID(fid_str)
        # Skip duplicates — they already have results
        row = db.get(FileModel, fid)
        if row and row.status == "queued":
            q.enqueue_sync(fid)

    return UploadResponse(
        file_ids=accepted_file_ids,
        operator=operator_name.strip(),
        status="queued",
        total_files=len(accepted_file_ids),
    )
