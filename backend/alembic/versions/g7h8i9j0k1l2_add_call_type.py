"""Add files.call_type for classification of non-classical calls

Звонки классифицируются после диаризации в pipeline. Non-classical (no_answer,
voicemail, internal, short) скрываются из API/UI/отчётов, но остаются в БД
для аудита.

Revision ID: g7h8i9j0k1l2
Revises: d5e6f7a8b9c0
Create Date: 2026-04-30 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "g7h8i9j0k1l2"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column(
            "call_type",
            sa.String(length=20),
            nullable=False,
            server_default="classical",
        ),
    )
    op.create_check_constraint(
        "ck_files_call_type",
        "files",
        "call_type IN ('classical', 'no_answer', 'voicemail', 'internal', 'short')",
    )
    op.create_index("idx_files_call_type", "files", ["call_type"])


def downgrade() -> None:
    op.drop_index("idx_files_call_type", table_name="files")
    op.drop_constraint("ck_files_call_type", "files", type_="check")
    op.drop_column("files", "call_type")
