"""Add revoked_tokens table for JWT revocation.

Revision ID: 2025_12_05_0014
Revises: 2025_12_04_0013
Create Date: 2025-12-05

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2025_12_05_0014"
down_revision: str | None = "2025_12_04_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create revoked_tokens table."""
    op.create_table(
        "revoked_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False, index=True),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_by", sa.String(255), nullable=False),
    )
    op.create_index("idx_revoked_tokens_user_id", "revoked_tokens", ["user_id"])
    op.create_index("idx_revoked_tokens_expires_at", "revoked_tokens", ["token_expires_at"])


def downgrade() -> None:
    """Drop revoked_tokens table."""
    op.drop_index("idx_revoked_tokens_expires_at", table_name="revoked_tokens")
    op.drop_index("idx_revoked_tokens_user_id", table_name="revoked_tokens")
    op.drop_table("revoked_tokens")
