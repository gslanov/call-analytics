"""POST /api/v1/upload — batch audio file upload with streaming I/O and dedup."""

import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import File as FileModel, Operator
from app.schemas import AcceptedFile, UploadResponse, ValidationError
from app.services.audio_validator import validate_audio_file_path
from app.services.queue import QueueManager
from app.utils import sanitize_filename, fix_encoding, parse_call_filename

router = APIRouter(tags=["upload"])

CHUNK_SIZE = 1 << 20  # 1 MB


def _get_or_create_operator(db: Session, name: str) -> Operator:
    op = db.scalar(select(Operator).where(Operator.name == name))
    if op is None:
        op = Operator(name=name)
        db.add(op)
        db.flush()
    return op


async def _stream_upload_to_disk(
    upload: UploadFile,
    file_id: uuid.UUID,
    ext: str,
) -> tuple[Path, str, int, str | None]:
    """Stream UploadFile to disk in chunks, computing SHA-256 incrementally.

    Returns:
        (final_path, sha256_hex, total_bytes, error_or_None)

    final_path is the temp `.tmp` path until validation passes — caller must
    rename to final after successful validation, or unlink on failure.
    """
    dest_dir = Path(settings.uploads_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_dir / f".{file_id}{ext}.tmp"

    hasher = hashlib.sha256()
    total = 0
    max_bytes = settings.max_file_size_bytes

    try:
        with open(tmp_path, "wb") as f:
            while True:
                chunk = await upload.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    f.close()
                    tmp_path.unlink(missing_ok=True)
                    return tmp_path, "", total, (
                        f"Размер файла превышает лимит {settings.max_file_size_mb} MB"
                    )
                f.write(chunk)
                hasher.update(chunk)
            f.flush()
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        return tmp_path, "", total, f"Ошибка записи: {exc}"

    return tmp_path, hasher.hexdigest(), total, None


@router.post("/upload", response_model=UploadResponse)
async def upload_files(
    files: list[UploadFile] = File(..., description="Аудиофайлы для анализа"),
    operator_name: str = Form("", description="Имя оператора (если пусто — берётся из имени файла)"),
    db: Session = Depends(get_db),
) -> UploadResponse:
    """Загрузить аудиофайлы для анализа качества звонка.

    Пишет файлы потоково (chunks по 1 MB), хеш SHA-256 считается на лету.
    Дедуплицирует по хешу. Atomic rename .tmp → final после успешной валидации.
    """
    operator_name = fix_encoding(operator_name).strip()
    if len(files) > settings.max_batch_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Слишком много файлов. Максимум {settings.max_batch_size} за раз",
        )

    existing_rows = db.execute(
        select(FileModel.file_hash, FileModel.id).where(
            FileModel.status != "failed"
        )
    ).all()
    hash_to_file_id: dict[str, uuid.UUID] = {row.file_hash: row.id for row in existing_rows}

    validation_errors: list[ValidationError] = []
    accepted_file_ids: list[str] = []
    accepted: list[AcceptedFile] = []

    for upload in files:
        filename = sanitize_filename(upload.filename or "unknown")
        ext = Path(filename).suffix.lower()
        file_id = uuid.uuid4()

        # Determine operator
        file_operator_name = operator_name
        if not file_operator_name:
            parsed = parse_call_filename(filename)
            file_operator_name = parsed.get("operator_name") or ""
        if not file_operator_name:
            file_operator_name = "Неизвестный оператор"
        operator = _get_or_create_operator(db, file_operator_name)

        # Stream to disk (.tmp file) with incremental SHA-256
        tmp_path, file_hash, total_bytes, stream_err = await _stream_upload_to_disk(
            upload, file_id, ext,
        )
        if stream_err:
            validation_errors.append(ValidationError(file=filename, error=stream_err))
            continue

        # Validate on disk
        result = validate_audio_file_path(
            filename,
            tmp_path,
            file_hash,
            total_bytes,
            existing_hashes=set(hash_to_file_id.keys()),
        )

        if not result.valid:
            tmp_path.unlink(missing_ok=True)
            if result.error and result.error.startswith("duplicate:"):
                fh = result.error.split(":", 1)[1]
                existing_id = hash_to_file_id.get(fh)
                if existing_id:
                    accepted_file_ids.append(str(existing_id))
                    accepted.append(AcceptedFile(
                        file_id=str(existing_id),
                        original_name=filename,
                        is_duplicate=True,
                    ))
                    continue
            validation_errors.append(ValidationError(file=filename, error=result.error or "Неизвестная ошибка"))
            continue

        # Atomic rename .tmp → final
        final_path = tmp_path.parent / f"{file_id}{ext}"
        tmp_path.rename(final_path)

        db_file = FileModel(
            id=file_id,
            operator_id=operator.id,
            original_name=filename,
            file_hash=result.file_hash,
            file_size=total_bytes,
            duration_sec=result.duration_sec,
            audio_path=str(final_path),
            status="queued",
            stage=0,
        )
        try:
            with db.begin_nested():
                db.add(db_file)
                db.flush()
        except IntegrityError:
            final_path.unlink(missing_ok=True)
            existing = db.scalar(
                select(FileModel.id).where(
                    FileModel.file_hash == result.file_hash,
                    FileModel.status != "failed",
                )
            )
            if existing:
                accepted_file_ids.append(str(existing))
                accepted.append(AcceptedFile(
                    file_id=str(existing),
                    original_name=filename,
                    is_duplicate=True,
                ))
            continue
        hash_to_file_id[result.file_hash] = file_id
        accepted_file_ids.append(str(file_id))
        accepted.append(AcceptedFile(
            file_id=str(file_id),
            original_name=filename,
            is_duplicate=False,
        ))

    # Если ВСЕ файлы упали валидацией — откат и 400 (frontend поймёт ошибку)
    if validation_errors and not accepted_file_ids:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "validation_error", "details": [e.model_dump() for e in validation_errors]},
        )

    db.commit()

    q = QueueManager.get_instance()
    for fid_str in accepted_file_ids:
        fid = uuid.UUID(fid_str)
        row = db.get(FileModel, fid)
        if row and row.status == "queued":
            try:
                q.enqueue_sync(fid)
            except Exception as exc:
                # Если очередь упала — помечаем файл failed, чтобы пользователь
                # увидел ошибку, а не висящий "queued" навсегда
                row.status = "failed"
                row.error_message = f"Не удалось поставить в очередь: {exc}"
                db.commit()
                validation_errors.append(ValidationError(
                    file=row.original_name,
                    error="Сервис очереди недоступен — попробуй позже",
                ))

    # Частичный успех: 200 с принятыми ids + список ошибок per-file
    return UploadResponse(
        accepted=accepted,
        file_ids=accepted_file_ids,
        operator=operator_name.strip(),
        status="queued",
        total_files=len(accepted_file_ids),
        validation_errors=validation_errors,
    )
