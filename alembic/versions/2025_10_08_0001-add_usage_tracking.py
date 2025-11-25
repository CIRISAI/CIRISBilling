"""add usage tracking columns

Revision ID: 2025_10_08_0001
Revises: 2025_10_08_0000
Create Date: 2025-10-08 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2025_10_08_0001"
down_revision: str | None = "2025_10_08_0000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add usage tracking columns to accounts table."""

    # Add free_uses_remaining column (default 3 for new accounts)
    op.add_column(
        "accounts",
        sa.Column("free_uses_remaining", sa.BigInteger(), nullable=False, server_default="3"),
    )

    # Add total_uses column (default 0)
    op.add_column(
        "accounts", sa.Column("total_uses", sa.BigInteger(), nullable=False, server_default="0")
    )

    # Add check constraints
    op.create_check_constraint("ck_free_uses_non_negative", "accounts", "free_uses_remaining >= 0")

    op.create_check_constraint("ck_total_uses_non_negative", "accounts", "total_uses >= 0")


def downgrade() -> None:
    """Remove usage tracking columns from accounts table."""

    # Drop check constraints
    op.drop_constraint("ck_total_uses_non_negative", "accounts", type_="check")
    op.drop_constraint("ck_free_uses_non_negative", "accounts", type_="check")

    # Drop columns
    op.drop_column("accounts", "total_uses")
    op.drop_column("accounts", "free_uses_remaining")
