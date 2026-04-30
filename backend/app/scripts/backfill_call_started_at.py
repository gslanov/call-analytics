"""Бэкфилл files.call_started_at из original_name.

Запускать ОДИН РАЗ после миграции h8i9j0k1l2m3:
    docker exec call-analytics-backend python -m app.scripts.backfill_call_started_at

Парсит дату+время из имён вида `2026-04-04__19-51-19__...` и заполняет поле
`call_started_at` для всех записей где оно NULL. Идемпотентно — повторный
запуск ничего не сломает.

ALTER+INDEX делаем тоже через `IF NOT EXISTS` — alembic-история разъехалась
(см. memory: project_alembic_drift), поэтому вместо `alembic upgrade head`
пробрасываем DDL вручную.
"""
from __future__ import annotations

from sqlalchemy import select, text

from app.database import SessionLocal
from app.models import File
from app.utils import parse_call_started_at


def ensure_schema(session) -> None:
    """ALTER+INDEX через IF NOT EXISTS — безопасно при повторном запуске."""
    session.execute(text(
        "ALTER TABLE files ADD COLUMN IF NOT EXISTS call_started_at TIMESTAMP NULL"
    ))
    session.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_files_call_started "
        "ON files (call_started_at)"
    ))
    session.commit()


def backfill(session) -> tuple[int, int, int]:
    """Возвращает (всего_просмотрено, обновлено, не_удалось_распарсить).

    Коммит per-row: если контейнер упадёт посередине, прогресс сохраняется,
    повторный запуск доделает оставшееся (фильтр `IS NULL` пропустит готовое).
    """
    rows = session.execute(
        select(File.id, File.original_name).where(File.call_started_at.is_(None))
    ).all()

    updated = 0
    failed = 0
    for row in rows:
        ts = parse_call_started_at(row.original_name or "")
        if ts is None:
            failed += 1
            continue
        try:
            session.execute(
                text("UPDATE files SET call_started_at = :ts WHERE id = :fid"),
                {"ts": ts, "fid": row.id},
            )
            session.commit()
            updated += 1
        except Exception as exc:
            session.rollback()
            print(f"  skip {row.id}: {exc}")
            failed += 1
    return len(rows), updated, failed


def main() -> None:
    with SessionLocal() as session:
        ensure_schema(session)
        total, updated, failed = backfill(session)
    print(
        f"call_started_at backfill: scanned={total}, updated={updated}, "
        f"unparseable={failed}"
    )


if __name__ == "__main__":
    main()
