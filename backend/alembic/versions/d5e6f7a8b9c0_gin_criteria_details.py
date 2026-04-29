"""Add GIN index on analyses.criteria_details for fast JSONB filtering

Используется в /reports/criteria и future-proof для фильтра «звонки где
конкретный критерий = false». Без индекса при росте >25k записей идёт
seq scan → таймаут.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7g8h9
Create Date: 2026-04-29 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, Sequence[str], None] = 'c4d5e6f7g8h9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Без CONCURRENTLY — БД небольшая, ACCESS EXCLUSIVE на доли секунды
    # допустим. На проде с >100k записей лучше применять CONCURRENTLY вручную:
    #   docker exec -i ... psql -c "CREATE INDEX CONCURRENTLY ..."
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_analyses_criteria_details_gin "
        "ON analyses USING GIN (criteria_details)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_analyses_criteria_details_gin")
