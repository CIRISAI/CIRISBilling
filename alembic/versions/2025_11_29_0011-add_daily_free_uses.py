"""add daily free uses support

Revision ID: 2025_11_29_0011
Revises: 2025_11_25_0010
Create Date: 2025-11-29 00:00:00.000000

Adds daily free uses feature:
- daily_free_uses_remaining: Number of free uses left today (resets daily)
- daily_free_uses_reset_at: When the daily uses should reset
- daily_free_uses_limit: Maximum daily free uses (configurable per account)
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2025_11_29_0011"
down_revision: str | None = "2025_11_25_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add daily free uses columns to accounts table
    op.add_column(
        "accounts",
        sa.Column(
            "daily_free_uses_remaining",
            sa.Integer(),
            nullable=False,
            server_default="2",
        ),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "daily_free_uses_reset_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "daily_free_uses_limit",
            sa.Integer(),
            nullable=False,
            server_default="2",
        ),
    )

    # Add check constraint for daily_free_uses_remaining
    op.create_check_constraint(
        "ck_daily_free_uses_non_negative",
        "accounts",
        "daily_free_uses_remaining >= 0",
    )

    # Add check constraint for daily_free_uses_limit
    op.create_check_constraint(
        "ck_daily_free_uses_limit_positive",
        "accounts",
        "daily_free_uses_limit > 0",
    )


def downgrade() -> None:
    # Drop check constraints
    op.drop_constraint("ck_daily_free_uses_limit_positive", "accounts", type_="check")
    op.drop_constraint("ck_daily_free_uses_non_negative", "accounts", type_="check")

    # Drop columns
    op.drop_column("accounts", "daily_free_uses_limit")
    op.drop_column("accounts", "daily_free_uses_reset_at")
    op.drop_column("accounts", "daily_free_uses_remaining")
