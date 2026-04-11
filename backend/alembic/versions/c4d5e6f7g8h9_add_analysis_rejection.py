"""Add rejected, rejection_reason, rejected_at to analyses

Revision ID: c4d5e6f7g8h9
Revises: b3c4d5e6f7a8
Create Date: 2026-04-11 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7g8h9'
down_revision: Union[str, Sequence[str], None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('analyses', sa.Column('rejected', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('analyses', sa.Column('rejection_reason', sa.Text(), nullable=True))
    op.add_column('analyses', sa.Column('rejected_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('analyses', 'rejected_at')
    op.drop_column('analyses', 'rejection_reason')
    op.drop_column('analyses', 'rejected')
