"""Add display_name to accounts table.

Revision ID: 2025_12_01_0012
Revises: 2025_11_29_0011
Create Date: 2025-12-01

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "2025_12_01_0012"
down_revision = "2025_11_29_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add display_name column to accounts table."""
    op.add_column(
        "accounts",
        sa.Column("display_name", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    """Remove display_name column from accounts table."""
    op.drop_column("accounts", "display_name")
