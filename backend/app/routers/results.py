"""GET /api/v1/results — paginated list and detail view of processed calls.
GET /api/v1/status/{file_id} — lightweight polling fallback.
"""

import math
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Analysis, File, Operator
from app.schemas import (
    AnalysisSchema,
    DiarizationDetail,
    DiarizationSegmentSchema,
    PaginatedResults,
    ResultDetail,
    ResultListItem,
)

router = APIRouter(tags=["results"])

STAGE_NAMES = {
    0: "Ожидание",
    1: "Транскрибация",
    2: "Диаризация",
    3: "Анализ",
    4: "Готово",
}


def _make_list_item(db_file: File) -> ResultListItem:
    analysis = None
    if db_file.analysis:
        analysis = AnalysisSchema.model_validate(db_file.analysis)
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
    db: Session = Depends(get_db),
) -> PaginatedResults:
    """Список обработанных звонков с пагинацией и фильтрацией."""
    query = (
        select(File)
        .options(
            joinedload(File.operator),
            joinedload(File.analysis),
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

    # Bug #3: use `limit` instead of `page_size`
    offset = (page - 1) * limit
    query = query.order_by(File.created_at.desc()).offset(offset).limit(limit)
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

    # Transcription
    full_text = db_file.transcription.full_text if db_file.transcription else None

    # Bug #5: nest diarization in DiarizationDetail object
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
        diarization=diarization_detail,
        analysis=analysis,
    )


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
        "error": db_file.error_message if db_file.status == "failed" else None,
    }
