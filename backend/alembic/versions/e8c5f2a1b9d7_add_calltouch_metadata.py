"""Add Calltouch metadata columns to files table

Revision ID: e8c5f2a1b9d7
Revises: ada2ee2d6132
Create Date: 2026-02-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8c5f2a1b9d7'
down_revision: Union[str, Sequence[str], None] = 'ada2ee2d6132'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: add Calltouch metadata columns."""
    # Add Calltouch metadata columns to files table
    op.add_column('files', sa.Column('callerphone', sa.String(32), nullable=True))
    op.add_column('files', sa.Column('calledphone', sa.String(32), nullable=True))
    op.add_column('files', sa.Column('operatorphone', sa.String(32), nullable=True))
    op.add_column('files', sa.Column('duration', sa.Integer(), nullable=True))
    op.add_column('files', sa.Column('order_id', sa.String(128), nullable=True))

    # Create index on order_id for filtering
    op.create_index('idx_files_order_id', 'files', ['order_id'])


def downgrade() -> None:
    """Downgrade schema: remove Calltouch metadata columns."""
    op.drop_index('idx_files_order_id', table_name='files')
    op.drop_column('files', 'order_id')
    op.drop_column('files', 'duration')
    op.drop_column('files', 'operatorphone')
    op.drop_column('files', 'calledphone')
    op.drop_column('files', 'callerphone')
