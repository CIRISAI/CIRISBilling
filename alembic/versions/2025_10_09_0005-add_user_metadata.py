"""add user metadata fields

Revision ID: 2025_10_09_0005
Revises: 2025_10_08_0004
Create Date: 2025-10-09 00:00:00.000000

Adds user metadata tracking to accounts table:
- user_role: string (admin, authority, observer)
- agent_id: string (unique agent ID making the request)
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2025_10_09_0005"
down_revision: str | None = "2025_10_08_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add user metadata columns
    op.add_column("accounts", sa.Column("user_role", sa.String(length=50), nullable=True))
    op.add_column("accounts", sa.Column("agent_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    # Drop user metadata columns
    op.drop_column("accounts", "agent_id")
    op.drop_column("accounts", "user_role")
