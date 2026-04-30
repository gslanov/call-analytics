"""Add files.call_started_at for filtering by actual call date

РОП фильтрует звонки за день по дате звонка (из имени файла Манго), а не по
дате загрузки в систему. Поле парсится из имени файла при загрузке + бэкфилл
существующих записей через parse_call_started_at.

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-04-30 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "h8i9j0k1l2m3"
down_revision: Union[str, Sequence[str], None] = "g7h8i9j0k1l2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column("call_started_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_files_call_started", "files", ["call_started_at"])


def downgrade() -> None:
    op.drop_index("idx_files_call_started", table_name="files")
    op.drop_column("files", "call_started_at")
