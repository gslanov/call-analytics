"""GET /api/v1/reports — aggregated reports per operator and overall.
GET /api/v1/reports/download — CSV export.
"""

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, case, and_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Analysis, File, Operator

router = APIRouter(tags=["reports"])


@router.get("/reports")
def get_reports(
    date_from: datetime | None = Query(None, description="Дата начала (ISO 8601)"),
    date_to: datetime | None = Query(None, description="Дата конца (ISO 8601)"),
    db: Session = Depends(get_db),
) -> dict:
    """Агрегированный отчёт: средние оценки по каждому оператору + общий по отделу.

    Исключает отклонённые (rejected) анализы.
    """
    # Base filter: done files with non-rejected analysis
    base_filter = and_(
        File.status == "done",
        Analysis.rejected == False,  # noqa: E712
    )
    if date_from:
        base_filter = and_(base_filter, File.created_at >= date_from)
    if date_to:
        base_filter = and_(base_filter, File.created_at <= date_to)

    # Per-operator stats
    operator_query = (
        select(
            Operator.name,
            func.count(Analysis.id).label("call_count"),
            func.round(func.avg(Analysis.standard)).label("avg_standard"),
            func.round(func.avg(Analysis.loyalty)).label("avg_loyalty"),
            func.round(func.avg(Analysis.kindness)).label("avg_kindness"),
            func.round(func.avg(Analysis.overall)).label("avg_overall"),
            func.min(Analysis.overall).label("min_overall"),
            func.max(Analysis.overall).label("max_overall"),
        )
        .join(File, File.id == Analysis.file_id)
        .join(Operator, Operator.id == File.operator_id)
        .where(base_filter)
        .group_by(Operator.name)
        .order_by(Operator.name)
    )
    operator_rows = db.execute(operator_query).all()

    operators = []
    for row in operator_rows:
        operators.append({
            "name": row.name,
            "call_count": row.call_count,
            "avg_standard": int(row.avg_standard or 0),
            "avg_loyalty": int(row.avg_loyalty or 0),
            "avg_kindness": int(row.avg_kindness or 0),
            "avg_overall": int(row.avg_overall or 0),
            "min_overall": int(row.min_overall or 0),
            "max_overall": int(row.max_overall or 0),
        })

    # Overall stats
    overall_query = (
        select(
            func.count(Analysis.id).label("call_count"),
            func.round(func.avg(Analysis.standard)).label("avg_standard"),
            func.round(func.avg(Analysis.loyalty)).label("avg_loyalty"),
            func.round(func.avg(Analysis.kindness)).label("avg_kindness"),
            func.round(func.avg(Analysis.overall)).label("avg_overall"),
            func.min(Analysis.overall).label("min_overall"),
            func.max(Analysis.overall).label("max_overall"),
        )
        .join(File, File.id == Analysis.file_id)
        .where(base_filter)
    )
    overall = db.execute(overall_query).one()

    # Rejected count (for info)
    rejected_count = db.scalar(
        select(func.count(Analysis.id))
        .join(File, File.id == Analysis.file_id)
        .where(
            File.status == "done",
            Analysis.rejected == True,  # noqa: E712
        )
    ) or 0

    return {
        "operators": operators,
        "overall": {
            "call_count": overall.call_count or 0,
            "avg_standard": int(overall.avg_standard or 0),
            "avg_loyalty": int(overall.avg_loyalty or 0),
            "avg_kindness": int(overall.avg_kindness or 0),
            "avg_overall": int(overall.avg_overall or 0),
            "min_overall": int(overall.min_overall or 0),
            "max_overall": int(overall.max_overall or 0),
        },
        "rejected_count": rejected_count,
        "filters": {
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
        },
    }


@router.get("/reports/download")
def download_report(
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Скачать отчёт в CSV (открывается в Excel)."""
    data = get_reports(date_from=date_from, date_to=date_to, db=db)

    output = io.StringIO()
    # BOM for Excel to detect UTF-8
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=";")

    writer.writerow(["Оператор", "Звонков", "Стандарты", "Лояльность", "Доброжелательность", "Средний", "Мин", "Макс"])
    for op in data["operators"]:
        writer.writerow([
            op["name"], op["call_count"],
            f'{op["avg_standard"]}%', f'{op["avg_loyalty"]}%',
            f'{op["avg_kindness"]}%', f'{op["avg_overall"]}%',
            f'{op["min_overall"]}%', f'{op["max_overall"]}%',
        ])
    writer.writerow([])
    o = data["overall"]
    writer.writerow([
        "ИТОГО", o["call_count"],
        f'{o["avg_standard"]}%', f'{o["avg_loyalty"]}%',
        f'{o["avg_kindness"]}%', f'{o["avg_overall"]}%',
        f'{o["min_overall"]}%', f'{o["max_overall"]}%',
    ])
    writer.writerow([])
    writer.writerow([f"Отклонённых оценок: {data['rejected_count']}"])

    output.seek(0)
    filename = f"report_{datetime.utcnow().strftime('%Y-%m-%d')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
