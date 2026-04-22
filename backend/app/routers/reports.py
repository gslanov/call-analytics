"""GET /api/v1/reports — aggregated reports per operator and overall.
GET /api/v1/reports/download — CSV export of operator summary.
GET /api/v1/reports/criteria — per-criterion pass rates across filtered calls.
GET /api/v1/reports/criteria/download — CSV export of per-criterion report.
"""

import csv
import io
from datetime import datetime
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Analysis, File, Operator
from app.services.llm_service import CRITERIA_LABELS, CRITERIA_SCHEMA

router = APIRouter(tags=["reports"])


GROUP_LABELS = {
    "standard": "Стандарты",
    "loyalty": "Лояльность",
    "kindness": "Доброжелательность",
    "markers": "Маркеры (информационные)",
}


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
    output.write("﻿")
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


def _compute_criteria_report(
    db: Session,
    date_from: datetime | None,
    date_to: datetime | None,
    operator: str | None,
) -> dict:
    """Считает процент выполнения по каждому из 22 критериев + 2 маркера.

    Алгоритм: загружаем все применимые analyses (~130/день × период), парсим
    criteria_details в Python. Для каждого критерия считаем:
      pass_count   — сколько раз значение true
      fail_count   — сколько раз false
      applicable   — pass + fail (null исключаются как «не применимо»)
      pass_rate    — pass_count / applicable * 100, либо null если applicable = 0
    """
    query = (
        select(Analysis.criteria_details)
        .join(File, File.id == Analysis.file_id)
        .where(
            File.status == "done",
            Analysis.rejected == False,  # noqa: E712
        )
    )
    if date_from is not None:
        query = query.where(File.created_at >= date_from)
    if date_to is not None:
        query = query.where(File.created_at <= date_to)
    if operator:
        query = query.join(Operator, Operator.id == File.operator_id).where(
            Operator.name.ilike(f"%{operator}%")
        )

    rows = db.execute(query).all()
    total_calls = len(rows)

    # Инициализируем счётчики для каждого (group, key) из схемы
    counters: dict[str, dict[str, dict[str, int]]] = {}
    for group, keys in CRITERIA_SCHEMA.items():
        counters[group] = {key: {"pass": 0, "fail": 0} for key in keys}

    for (criteria_details,) in rows:
        if not criteria_details:
            continue
        for group, keys in CRITERIA_SCHEMA.items():
            group_data = criteria_details.get(group) or {}
            for key in keys:
                value = group_data.get(key)
                if value is True:
                    counters[group][key]["pass"] += 1
                elif value is False:
                    counters[group][key]["fail"] += 1
                # None/отсутствует → «не применимо», не учитываем

    groups_out: dict[str, dict] = {}
    for group, keys in CRITERIA_SCHEMA.items():
        criteria_list = []
        for key in keys:
            pass_count = counters[group][key]["pass"]
            fail_count = counters[group][key]["fail"]
            applicable = pass_count + fail_count
            pass_rate: int | None = (
                round(pass_count / applicable * 100) if applicable > 0 else None
            )
            criteria_list.append({
                "key": key,
                "label": CRITERIA_LABELS.get(group, {}).get(key, key),
                "pass_count": pass_count,
                "fail_count": fail_count,
                "applicable_count": applicable,
                "pass_rate": pass_rate,
            })
        groups_out[group] = {
            "label": GROUP_LABELS.get(group, group),
            "criteria": criteria_list,
        }

    return {
        "total_calls": total_calls,
        "filters": {
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "operator": operator,
        },
        "groups": groups_out,
    }


@router.get("/reports/criteria")
def get_criteria_report(
    date_from: datetime | None = Query(None, description="Дата начала (ISO 8601)"),
    date_to: datetime | None = Query(None, description="Дата конца (ISO 8601)"),
    operator: str | None = Query(None, description="Фильтр по имени оператора (LIKE)"),
    db: Session = Depends(get_db),
) -> dict:
    """Процент выполнения по каждому из 22 критериев и 2 маркеров.

    Показывает: «из X применимых звонков Y выполнены (Z%)».
    Звонки, где критерий помечен null (не применимо) — исключаются из знаменателя.
    """
    return _compute_criteria_report(db, date_from, date_to, operator)


def _content_disposition(filename: str) -> str:
    """RFC 5987: ASCII fallback + UTF-8 для кириллицы в имени файла."""
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
    utf8_quoted = url_quote(filename)
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{utf8_quoted}"


@router.get("/reports/criteria/download")
def download_criteria_report(
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    operator: str | None = Query(None),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Скачать отчёт по критериям в CSV (открывается в Excel)."""
    data = _compute_criteria_report(db, date_from, date_to, operator)

    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, delimiter=";")

    # Шапка с метаданными — помогает клиенту понять, по каким фильтрам это
    writer.writerow(["Всего звонков:", data["total_calls"]])
    if data["filters"]["operator"]:
        writer.writerow(["Оператор:", data["filters"]["operator"]])
    if data["filters"]["date_from"] or data["filters"]["date_to"]:
        writer.writerow([
            "Период:",
            data["filters"]["date_from"] or "—",
            data["filters"]["date_to"] or "—",
        ])
    writer.writerow([])

    writer.writerow(["Группа", "Критерий", "Применимо звонков", "Выполнено", "Не выполнено", "Процент"])
    for group_key, group_data in data["groups"].items():
        for c in group_data["criteria"]:
            pass_rate = c["pass_rate"]
            writer.writerow([
                group_data["label"],
                c["label"],
                c["applicable_count"],
                c["pass_count"],
                c["fail_count"],
                f"{pass_rate}%" if pass_rate is not None else "—",
            ])

    output.seek(0)

    parts = ["criteria"]
    if operator:
        parts.append(operator)
    if date_from:
        parts.append(date_from.strftime("%Y-%m-%d"))
    if date_to:
        parts.append(date_to.strftime("%Y-%m-%d"))
    if len(parts) == 1:
        parts.append(datetime.utcnow().strftime("%Y-%m-%d"))
    filename = "_".join(parts) + ".csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(filename)},
    )
