"""add marketing consent fields

Revision ID: 2025_10_08_0004
Revises: 2025_10_08_0003
Create Date: 2025-10-08 12:30:00.000000

Adds marketing consent tracking to accounts table for GDPR compliance:
- marketing_opt_in: bool (consent status)
- marketing_opt_in_at: timestamp (when consent was given)
- marketing_opt_in_source: string (where consent was obtained)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '2025_10_08_0004'
down_revision: Union[str, None] = '2025_10_08_0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add marketing consent columns
    op.add_column('accounts', sa.Column('marketing_opt_in', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('accounts', sa.Column('marketing_opt_in_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('accounts', sa.Column('marketing_opt_in_source', sa.String(length=50), nullable=True))


def downgrade() -> None:
    # Drop marketing consent columns
    op.drop_column('accounts', 'marketing_opt_in_source')
    op.drop_column('accounts', 'marketing_opt_in_at')
    op.drop_column('accounts', 'marketing_opt_in')
