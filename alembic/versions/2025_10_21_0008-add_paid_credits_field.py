"""add paid_credits field

Revision ID: 2025_10_21_0008
Revises: 2025_10_16_0007
Create Date: 2025-10-21 00:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2025_10_21_0008'
down_revision: Union[str, None] = '2025_10_16_0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add paid_credits column to accounts table
    op.add_column('accounts', sa.Column('paid_credits', sa.BigInteger(), nullable=False, server_default='0'))

    # Add check constraint for non-negative paid_credits
    op.create_check_constraint(
        'ck_paid_credits_non_negative',
        'accounts',
        'paid_credits >= 0'
    )

    # Migrate existing balance_minor values to paid_credits
    # (assuming balance_minor was being used to store credits)
    op.execute('UPDATE accounts SET paid_credits = balance_minor WHERE balance_minor > 0')

    # Reset balance_minor to 0 (will be used for actual currency in future)
    op.execute('UPDATE accounts SET balance_minor = 0')


def downgrade() -> None:
    # Migrate paid_credits back to balance_minor
    op.execute('UPDATE accounts SET balance_minor = paid_credits WHERE paid_credits > 0')

    # Drop check constraint
    op.drop_constraint('ck_paid_credits_non_negative', 'accounts', type_='check')

    # Drop column
    op.drop_column('accounts', 'paid_credits')
