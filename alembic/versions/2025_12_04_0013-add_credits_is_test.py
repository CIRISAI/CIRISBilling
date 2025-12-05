"""Add is_test field to credits table.

Revision ID: 2025_12_04_0013
Revises: 2025_12_01_0012
Create Date: 2025-12-04

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2025_12_04_0013"
down_revision: str | None = "2025_12_01_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add is_test column to credits table."""
    op.add_column(
        "credits",
        sa.Column("is_test", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    """Remove is_test column from credits table."""
    op.drop_column("credits", "is_test")
