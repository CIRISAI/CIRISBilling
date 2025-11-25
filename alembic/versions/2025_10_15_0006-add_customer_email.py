"""add customer_email to accounts

Revision ID: 2025_10_15_0006
Revises: 2025_10_09_0005
Create Date: 2025-10-15 00:00:00.000000

Adds customer_email field to accounts table for storing user email addresses.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2025_10_15_0006"
down_revision: str | None = "2025_10_09_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add customer_email column
    op.add_column("accounts", sa.Column("customer_email", sa.String(length=255), nullable=True))


def downgrade() -> None:
    # Drop customer_email column
    op.drop_column("accounts", "customer_email")
