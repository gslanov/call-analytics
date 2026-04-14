"""GET /api/v1/results — paginated list and detail view of processed calls.
GET /api/v1/status/{file_id} — lightweight polling fallback.
"""

import math
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import asc, desc, func, nulls_last, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.models import Analysis, File, Operator
from app.schemas import (
    AnalysisSchema,
    DiarizationDetail,
    DiarizationSegmentSchema,
    PaginatedResults,
    ResultDetail,
    ResultListItem,
    TranscriptionDetail,
)

router = APIRouter(tags=["results"])

STAGE_NAMES = {
    0: "Ожидание",
    1: "Транскрибация",
    2: "Диаризация",
    3: "Анализ",
    4: "Готово",
}

# Whitelist допустимых полей для сортировки (защита от SQL injection)
SORT_COLUMNS = {
    "created_at": File.created_at,
    "operator_name": Operator.name,
    "overall": Analysis.overall,
    "standard": Analysis.standard,
    "loyalty": Analysis.loyalty,
    "kindness": Analysis.kindness,
}


def _make_list_item(db_file: File) -> ResultListItem:
    from app.utils import parse_call_filename
    analysis = None
    if db_file.analysis:
        analysis = AnalysisSchema.model_validate(db_file.analysis)
    call_info = parse_call_filename(db_file.original_name)
    return ResultListItem(
        file_id=db_file.id,
        original_name=db_file.original_name,
        operator_id=db_file.operator_id,
        operator_name=db_file.operator.name if db_file.operator else None,
        file_size=db_file.file_size,
        duration_sec=db_file.duration_sec,
        status=db_file.status,
        stage=db_file.stage or 0,
        progress=db_file.progress or 0,
        created_at=db_file.created_at,
        analysis=analysis,
        diarization_method=db_file.diarization.method if db_file.diarization else None,
        call_date=call_info["call_date"],
        call_time=call_info["call_time"],
        caller_phone=call_info["caller_phone"],
    )


@router.get("/results", response_model=PaginatedResults)
def list_results(
    page: int = Query(1, ge=1, description="Номер страницы"),
    limit: int = Query(20, ge=1, le=100, description="Элементов на странице"),  # Bug #3
    operator: str | None = Query(None, description="Фильтр по имени оператора (LIKE)"),  # Bug #4
    status_filter: str | None = Query(None, alias="status", description="Фильтр по статусу"),
    date_from: datetime | None = Query(None, description="Дата начала (ISO 8601)"),
    date_to: datetime | None = Query(None, description="Дата конца (ISO 8601)"),
    score_min: int | None = Query(None, ge=0, le=100, description="Минимальный overall score"),
    score_max: int | None = Query(None, ge=0, le=100, description="Максимальный overall score"),
    q: str | None = Query(None, description="Поиск по имени файла"),
    sort: str | None = Query(None, description="Поле сортировки: created_at, operator_name, overall, standard, loyalty, kindness"),
    order: str | None = Query("desc", description="Направление: asc или desc"),
    db: Session = Depends(get_db),
) -> PaginatedResults:
    """Список обработанных звонков с пагинацией и фильтрацией."""
    query = (
        select(File)
        .options(
            selectinload(File.operator),
            selectinload(File.analysis),
            selectinload(File.diarization),
        )
    )

    # Bug #4: filter by operator name (LIKE), not UUID
    if operator:
        query = query.join(Operator, Operator.id == File.operator_id).where(
            Operator.name.ilike(f"%{operator}%")
        )

    if status_filter is not None:
        query = query.where(File.status == status_filter)
    if date_from is not None:
        query = query.where(File.created_at >= date_from)
    if date_to is not None:
        query = query.where(File.created_at <= date_to)
    if q:
        query = query.where(File.original_name.ilike(f"%{q}%"))

    # Score filtering requires join with analyses
    if score_min is not None or score_max is not None:
        query = query.join(Analysis, Analysis.file_id == File.id)
        if score_min is not None:
            query = query.where(Analysis.overall >= score_min)
        if score_max is not None:
            query = query.where(Analysis.overall <= score_max)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = db.scalar(count_query) or 0

    # Динамическая сортировка по запросу фронтенда
    sort_col = SORT_COLUMNS.get(sort) if sort else None
    if sort_col is not None:
        # Если сортируем по полю Analysis — нужен outerjoin (если ещё не добавлен)
        if sort in ("overall", "standard", "loyalty", "kindness") and score_min is None and score_max is None:
            query = query.outerjoin(Analysis, Analysis.file_id == File.id)
        # Если сортируем по operator_name — нужен join (если ещё не добавлен через фильтр)
        if sort == "operator_name" and not operator:
            query = query.outerjoin(Operator, Operator.id == File.operator_id)
        direction = asc if order == "asc" else desc
        query = query.order_by(nulls_last(direction(sort_col)))
    else:
        query = query.order_by(File.created_at.desc())

    offset = (page - 1) * limit
    query = query.offset(offset).limit(limit)
    files = db.scalars(query).unique().all()

    items = [_make_list_item(f) for f in files]
    pages = math.ceil(total / limit) if total > 0 else 1

    return PaginatedResults(
        items=items,
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )


@router.get("/results/{file_id}", response_model=ResultDetail)
def get_result(
    file_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> ResultDetail:
    """Полные данные по одному звонку: транскрипция, диаризация, анализ."""
    db_file = db.scalar(
        select(File)
        .options(
            joinedload(File.operator),
            joinedload(File.transcription),
            joinedload(File.diarization),
            joinedload(File.analysis),
        )
        .where(File.id == file_id)
    )
    if db_file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    # Transcription (nested — фронт ожидает transcription.full_text)
    full_text = db_file.transcription.full_text if db_file.transcription else None
    transcription_detail = None
    if db_file.transcription:
        transcription_detail = TranscriptionDetail(
            full_text=db_file.transcription.full_text,
            word_timestamps=db_file.transcription.word_timestamps,
        )

    # Diarization (nested, с num_speakers)
    diarization_detail = None
    if db_file.diarization:
        segments = [
            DiarizationSegmentSchema(
                speaker=seg["speaker"],
                start=seg["start"],
                end=seg["end"],
                text=seg["text"],
            )
            for seg in (db_file.diarization.segments or [])
        ]
        diarization_detail = DiarizationDetail(
            method=db_file.diarization.method,
            confidence=db_file.diarization.confidence,
            num_speakers=db_file.diarization.num_speakers,
            segments=segments,
        )

    # Analysis
    analysis = None
    if db_file.analysis:
        analysis = AnalysisSchema.model_validate(db_file.analysis)

    return ResultDetail(
        file_id=db_file.id,
        original_name=db_file.original_name,
        operator_id=db_file.operator_id,
        operator_name=db_file.operator.name if db_file.operator else None,
        file_size=db_file.file_size,
        duration_sec=db_file.duration_sec,
        status=db_file.status,
        stage=db_file.stage or 0,
        progress=db_file.progress or 0,
        error_message=db_file.error_message,
        created_at=db_file.created_at,
        updated_at=db_file.updated_at,
        full_text=full_text,
        transcription=transcription_detail,
        diarization=diarization_detail,
        analysis=analysis,
    )


@router.delete("/results/{file_id}")
def delete_file(
    file_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Удалить звонок: файл с диска, транскрипция, диаризация, анализ."""
    from pathlib import Path
    from app.models import Transcription, Diarization

    db_file = db.get(File, file_id)
    if db_file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    # Remove related records
    for model in [Analysis, Diarization, Transcription]:
        existing = db.scalar(select(model).where(model.file_id == file_id))
        if existing:
            db.delete(existing)

    # Remove audio file from disk
    if db_file.audio_path:
        audio = Path(db_file.audio_path)
        if audio.exists():
            audio.unlink()

    # Remove DB record
    db.delete(db_file)
    db.commit()

    return {"file_id": str(file_id), "deleted": True}


@router.post("/results/{file_id}/reject")
def reject_analysis(
    file_id: uuid.UUID,
    body: dict,
    db: Session = Depends(get_db),
) -> dict:
    """РОП не согласен с оценкой — отклонить анализ. Сохраняется для калибровки."""
    from datetime import datetime as dt

    db_file = db.get(File, file_id)
    if db_file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    analysis = db.scalar(select(Analysis).where(Analysis.file_id == file_id))
    if analysis is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found")

    reason = body.get("reason", "").strip()
    if not reason:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Укажите причину отклонения")

    analysis.rejected = True
    analysis.rejection_reason = reason
    analysis.rejected_at = dt.utcnow()
    db.commit()

    return {
        "file_id": str(file_id),
        "rejected": True,
        "reason": reason,
        "old_scores": {
            "standard": analysis.standard,
            "loyalty": analysis.loyalty,
            "kindness": analysis.kindness,
            "overall": analysis.overall,
        },
    }


@router.patch("/results/{file_id}/criteria")
def update_criteria(
    file_id: uuid.UUID,
    body: dict,
    db: Session = Depends(get_db),
) -> dict:
    """РОП корректирует отдельные критерии — пересчёт оценок.

    Body: {"group": "standard", "key": "introduced_self", "value": true}
    Returns: updated scores + full criteria_details.
    """
    analysis = db.scalar(select(Analysis).where(Analysis.file_id == file_id))
    if analysis is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found")

    group = body.get("group")  # standard / loyalty / kindness
    key = body.get("key")      # criterion key
    value = body.get("value")  # true / false / null

    if group not in ("standard", "loyalty", "kindness"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid group")
    if not key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing key")
    if value is not None and not isinstance(value, bool):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Value must be true, false, or null")

    cd = dict(analysis.criteria_details or {})
    group_data = dict(cd.get(group, {}))
    if key not in group_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown criterion: {group}.{key}")

    # Update the criterion value
    group_data[key] = value
    cd[group] = group_data

    # Mark as manually edited in reasons
    reasons = dict(cd.get("reasons", {}))
    group_reasons = dict(reasons.get(group, {}))
    if value is True and key in group_reasons:
        group_reasons[key] = f"[Ручная правка РОП] {group_reasons.get(key, '')}"
    elif value is False and key not in group_reasons:
        group_reasons[key] = "[Ручная правка РОП]"
    reasons[group] = group_reasons
    cd["reasons"] = reasons

    analysis.criteria_details = cd

    # Recalculate scores per group
    def calc_group_score(items: dict) -> int:
        applicable = [v for v in items.values() if v is not None and isinstance(v, bool)]
        if not applicable:
            return 100
        passed = sum(1 for v in applicable if v is True)
        return round(passed / len(applicable) * 100)

    analysis.standard = calc_group_score(cd.get("standard", {}))
    analysis.loyalty = calc_group_score(cd.get("loyalty", {}))
    analysis.kindness = calc_group_score(cd.get("kindness", {}))
    analysis.overall = round(
        analysis.standard * 0.4 + analysis.loyalty * 0.3 + analysis.kindness * 0.3
    )

    db.commit()

    return {
        "file_id": str(file_id),
        "standard": analysis.standard,
        "loyalty": analysis.loyalty,
        "kindness": analysis.kindness,
        "overall": analysis.overall,
        "criteria_details": analysis.criteria_details,
    }


@router.post("/reprocess/{file_id}")
def reprocess_file(
    file_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Перезапустить обработку файла: сбросить результаты и поставить в очередь."""
    from app.models import Transcription, Diarization
    from app.services.queue import QueueManager

    db_file = db.get(File, file_id)
    if db_file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    # Удаляем предыдущие результаты
    for model in [Analysis, Diarization, Transcription]:
        existing = db.scalar(select(model).where(model.file_id == file_id))
        if existing:
            db.delete(existing)

    db_file.status = "queued"
    db_file.stage = 0
    db_file.progress = 0
    db_file.error_message = None
    db.commit()

    # Ставим в очередь (в том же процессе — queue worker подхватит)
    q = QueueManager.get_instance()
    q.enqueue_sync(file_id)

    return {"file_id": str(file_id), "status": "queued"}


# Bug #2: polling fallback endpoint for when WebSocket is unavailable
@router.get("/status/{file_id}")
def get_file_status(
    file_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Polling fallback: текущий статус файла (используется при недоступности WS)."""
    db_file = db.get(File, file_id)
    if db_file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    stage = db_file.stage or 0
    return {
        "file_id": str(db_file.id),
        "status": db_file.status,
        "progress": db_file.progress or 0,
        "stage": stage,
        "stage_name": STAGE_NAMES.get(stage, ""),
        "error_message": db_file.error_message if db_file.status == "failed" else None,
    }
