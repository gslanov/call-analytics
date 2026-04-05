"""Add file_hash partial unique index, status CHECK, progress CHECK

Revision ID: f1a2b3c4d5e6
Revises: e8c5f2a1b9d7
Create Date: 2026-04-05 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'e8c5f2a1b9d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 2.3: Partial unique index — один хэш = один активный файл
    # Позволяет повторную загрузку если предыдущая попытка failed
    op.create_index(
        "uq_files_hash_active",
        "files",
        ["file_hash"],
        unique=True,
        postgresql_where=sa.text("status != 'failed'"),
    )

    # 2.7: CHECK constraint на допустимые статусы
    op.create_check_constraint(
        "ck_files_status",
        "files",
        "status IN ('queued', 'transcribing', 'diarizing', 'analyzing', 'done', 'failed')",
    )

    # 2.7: CHECK constraint на диапазон прогресса
    op.create_check_constraint(
        "ck_files_progress",
        "files",
        "progress BETWEEN 0 AND 100",
    )


def downgrade() -> None:
    op.drop_constraint("ck_files_progress", "files", type_="check")
    op.drop_constraint("ck_files_status", "files", type_="check")
    op.drop_index("uq_files_hash_active", table_name="files")
