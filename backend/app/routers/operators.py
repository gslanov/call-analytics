"""GET /api/v1/operators — operator list with autocomplete and detail."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import File, Operator
from app.schemas import OperatorDetailSchema, OperatorSchema

router = APIRouter(tags=["operators"])


@router.get("/operators", response_model=list[str])
def list_operators(
    q: str | None = Query(None, description="Поиск по имени оператора (autocomplete)"),
    limit: int = Query(20, ge=1, le=100, description="Максимум результатов"),
    db: Session = Depends(get_db),
) -> list[str]:
    """Список имён операторов для autocomplete. Возвращает ['Иван', 'Петр', ...]."""
    # Bug #1: return list of names (strings), not objects
    query = select(Operator.name).distinct().order_by(Operator.name)

    if q:
        query = query.where(Operator.name.ilike(f"%{q}%"))

    query = query.limit(limit)
    rows = db.scalars(query).all()
    return list(rows)


@router.get("/operators/{operator_id}", response_model=OperatorDetailSchema)
def get_operator(
    operator_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> OperatorDetailSchema:
    """Информация об операторе с количеством звонков."""
    op = db.get(Operator, operator_id)
    if op is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operator not found")

    file_count = db.scalar(
        select(func.count(File.id)).where(File.operator_id == operator_id)
    ) or 0

    return OperatorDetailSchema(
        id=op.id,
        name=op.name,
        created_at=op.created_at,
        file_count=file_count,
    )
