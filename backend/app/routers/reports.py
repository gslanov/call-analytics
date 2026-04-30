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
from sqlalchemy import and_, func, select, text
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
    # Base filter: done classical files with non-rejected analysis.
    # Non-classical (no_answer/voicemail/internal/short) исключаются из всех отчётов.
    base_filter = and_(
        File.status == "done",
        File.call_type == "classical",
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
            File.call_type == "classical",
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

    Считает агрегацию ЦЕЛИКОМ в SQL через `COUNT(*) FILTER (WHERE jsonb-path = 'true')`
    — один запрос вместо тысяч строк в Python. Использует GIN-индекс на
    criteria_details (миграция d5e6f7a8b9c0).

    Совместимость: criteria_details может быть в новом формате
    `{"value": bool, "reason": "...", "timestamp": "..."}` или старом плоском
    `{key: bool|null}`. COALESCE проверяет оба пути.
    """
    # Безопасность: group/key приходят из CRITERIA_SCHEMA (whitelist в коде).
    # SQL injection невозможна — никакого user input в идентификаторах.
    filter_columns: list[str] = []
    for group, keys in CRITERIA_SCHEMA.items():
        for key in keys:
            new_path = f"analyses.criteria_details->'{group}'->'{key}'->>'value'"
            old_path = f"analyses.criteria_details->'{group}'->>'{key}'"
            value_expr = f"COALESCE({new_path}, {old_path})"
            filter_columns.append(
                f"COUNT(*) FILTER (WHERE {value_expr} = 'true') "
                f"AS \"{group}__{key}__pass\""
            )
            filter_columns.append(
                f"COUNT(*) FILTER (WHERE {value_expr} = 'false') "
                f"AS \"{group}__{key}__fail\""
            )

    where_parts: list[str] = [
        "files.status = 'done'",
        "files.call_type = 'classical'",
        "analyses.rejected = false",
    ]
    params: dict = {}
    operator_join = ""
    if date_from is not None:
        where_parts.append("files.created_at >= :date_from")
        params["date_from"] = date_from
    if date_to is not None:
        where_parts.append("files.created_at <= :date_to")
        params["date_to"] = date_to
    if operator:
        operator_join = "JOIN operators ON operators.id = files.operator_id"
        where_parts.append("operators.name ILIKE :op_pattern")
        params["op_pattern"] = f"%{operator}%"

    sql = text(f"""
        SELECT
            COUNT(*) AS total_calls,
            {', '.join(filter_columns)}
        FROM analyses
        JOIN files ON files.id = analyses.file_id
        {operator_join}
        WHERE {' AND '.join(where_parts)}
    """)

    row = db.execute(sql, params).mappings().one()
    total_calls = int(row["total_calls"] or 0)

    counters: dict[str, dict[str, dict[str, int]]] = {}
    for group, keys in CRITERIA_SCHEMA.items():
        counters[group] = {}
        for key in keys:
            counters[group][key] = {
                "pass": int(row.get(f"{group}__{key}__pass") or 0),
                "fail": int(row.get(f"{group}__{key}__fail") or 0),
            }

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
