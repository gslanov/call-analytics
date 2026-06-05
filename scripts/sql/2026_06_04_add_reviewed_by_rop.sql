-- Ручная миграция (alembic-история разъехалась — применять через psql, НЕ `alembic upgrade head`).
-- Добавляет колонки метки «проверено РОП» в files. Идемпотентно — безопасно гонять повторно.
--
-- Применение (тест, затем прод):
--   docker exec -i call-analytics-db psql -U callanalytics -d callanalytics < scripts/sql/2026_06_04_add_reviewed_by_rop.sql
-- или одной строкой:
--   docker exec call-analytics-db psql -U callanalytics -d callanalytics -c "ALTER TABLE files ADD COLUMN IF NOT EXISTS reviewed_by_rop boolean NOT NULL DEFAULT false; ALTER TABLE files ADD COLUMN IF NOT EXISTS reviewed_at timestamp without time zone;"
--
-- ВАЖНО: применять колонки ДО (или одновременно с) деплоя нового backend-кода —
-- модель SQLAlchemy File теперь ожидает эти колонки, иначе любой SELECT File упадёт.

ALTER TABLE files ADD COLUMN IF NOT EXISTS reviewed_by_rop boolean NOT NULL DEFAULT false;
ALTER TABLE files ADD COLUMN IF NOT EXISTS reviewed_at timestamp without time zone;
